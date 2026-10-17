"""Direct stock polling. No browser navigation, login, or order submission."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import html
import http.client
import json
import logging
import re
import ssl
import threading
import time
from urllib.parse import urlencode

from web_server import CATALOG, STORE_CATALOG, pickup_matches_date, set_state, worker_action

HOST = 'www.apple.com.cn'
MAX_CONCURRENT = 2
REQUEST_GAP = 1.0
REFRESH_SECONDS = 30
MAX_BACKOFF = 300


class StockError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def plain(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', str(text or '')))).strip()


def parse_stock(payload, parts):
    """Missing/unknown fields are unknown, never implicit out-of-stock."""
    if not isinstance(payload, dict):
        raise StockError('库存响应不是 JSON 对象')
    pickup = payload.get('body', {}).get('content', {}).get('pickupMessage', {})
    stores = pickup.get('stores')
    if not isinstance(stores, list) or not stores:
        raise StockError('响应未包含可读取的门店库存，保留未知状态')
    known = {s['id']: s for s in STORE_CATALOG}
    by_name = {s['name']: s for s in STORE_CATALOG}
    updates = []
    for store in stores:
        if not isinstance(store, dict):
            continue
        identity = known.get(store.get('storeNumber')) or by_name.get(plain(store.get('storeName')).removeprefix('Apple '))
        if not identity:
            continue
        availability = store.get('partsAvailability', {})
        if not isinstance(availability, dict):
            continue
        for part in parts:
            entry = availability.get(part)
            if not isinstance(entry, dict):
                continue
            display = entry.get('pickupDisplay')
            messages = entry.get('messageTypes', {}).get('regular', {})
            quote = plain(entry.get('pickupSearchQuote') or messages.get('storePickupQuote'))
            if display == 'available' and quote:
                updates.append((part, {'store': identity['name'], 'pickup': quote,
                                       'available': bool(pickup_matches_date(quote))}))
            elif display in {'unavailable', 'ineligible'}:
                updates.append((part, {'store': identity['name'], 'pickup': quote, 'available': False}))
    if not updates:
        raise StockError('响应中缺少已知商品的库存字段，保留未知状态')
    return updates


class StockClient:
    """At most two reusable connections; aggregate request starts <= one/second."""
    def __init__(self):
        self.local = threading.local()
        self.connections = []
        self.lock = threading.Lock()
        self.next_request = 0

    def query(self, store, parts, cancelled):
        with self.lock:
            delay = max(0, self.next_request - time.monotonic())
            self.next_request = max(self.next_request, time.monotonic()) + REQUEST_GAP
        if cancelled.wait(delay) or cancelled.is_set():
            raise StockError('查询已取消')
        connection = getattr(self.local, 'connection', None)
        if connection is None:
            connection = http.client.HTTPSConnection(HOST, timeout=10, context=ssl.create_default_context())
            self.local.connection = connection
            with self.lock:
                self.connections.append(connection)
        params = {'pl': 'true', 'mts.0': 'regular', 'store': store['id'], 'searchNearby': 'true'}
        params.update({'parts.' + str(i): part for i, part in enumerate(parts)})
        path = '/shop/fulfillment-messages?' + urlencode(params)
        try:
            connection.request('GET', path, headers={'Accept': 'application/json', 'User-Agent': 'AppleStoreMonitor/1.3'})
            response = connection.getresponse()
            raw = response.read(2_000_001)
            if response.status != 200:
                raise StockError('Apple 库存接口返回 HTTP ' + str(response.status), response.status)
            if len(raw) > 2_000_000:
                raise StockError('库存响应超出预期大小')
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError) as error:
                raise StockError('库存接口未返回 JSON，保留未知状态') from error
            return parse_stock(payload, parts)
        except Exception:
            connection.close()
            self.local.connection = None
            raise

    def close(self):
        for connection in self.connections:
            connection.close()


class FastStockMonitor:
    def __init__(self, stop, client=None):
        self.stop = stop
        self.client = client or StockClient()
        self.thread = None
        self.cancelled = threading.Event()
        self.checked = {}
        self.attempts = {}
        self.last_request_seconds = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def join(self):
        if self.thread:
            self.thread.join(timeout=12)

    def publish(self, message, **fields):
        set_state('stock_scan', {'mode': 'api', 'pairs': len(self.checked),
                               'total_pairs': len(CATALOG) * len(STORE_CATALOG), **fields})
        worker_action({'action': 'heartbeat', 'message': message})

    def run(self):
        parts = [p['part'] for p in CATALOG]
        pending = {}
        failures = 0
        retry_at = 0
        last_publish = 0
        refresh_seen = 0
        previous_enabled = False
        started = time.monotonic()
        pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT)
        try:
            while not self.stop.is_set():
                state = worker_action({'action': 'status'})['state']
                enabled = state.get('monitoring', True)
                now = time.monotonic()
                if not enabled:
                    self.cancelled.set()
                elif not previous_enabled:
                    # Never let a response begun before pause overwrite fresh state.
                    if not pending:
                        self.cancelled = threading.Event()
                requested = float(state.get('refresh_requested', 0))
                if requested > refresh_seen:
                    refresh_seen = requested
                    self.checked.clear()
                    self.attempts.clear()
                previous_enabled = enabled
                if pending:
                    done, _ = wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
                    for future in done:
                        anchor, generation, submitted = pending.pop(future)
                        self.last_request_seconds = round(time.monotonic() - submitted, 2)
                        if generation.is_set() or not enabled:
                            continue
                        try:
                            updates = future.result()
                            grouped = {}
                            for part, item in updates:
                                grouped.setdefault(part, []).append(item)
                            for part, items in grouped.items():
                                worker_action({'action': 'stock', 'part': part, 'stores': items})
                                for item in items:
                                    self.checked[(part, item['store'])] = time.monotonic()
                            failures = 0
                            set_state('stock_last_success', time.time())
                            set_state('stock_last_error', '')
                        except Exception as error:
                            failures += 1
                            delay = min(MAX_BACKOFF, 30 * 2 ** min(failures - 1, 4))
                            retry_at = max(retry_at, time.monotonic() + delay)
                            set_state('stock_last_error', str(error)[:180])
                            logging.warning('Stock query failed: %s', error)
                if enabled and self.cancelled.is_set() and not pending:
                    self.cancelled = threading.Event()
                if enabled and now >= retry_at and not self.cancelled.is_set():
                    busy = {anchor['id'] for anchor, _, _ in pending.values()}
                    priority = state.get('priority_store', '')
                    candidates = sorted(STORE_CATALOG, key=lambda s: (
                        s['name'] != priority,
                        max(self.attempts.get(s['id'], 0), min(self.checked.get((p, s['name']), 0) for p in parts))))
                    for store in candidates:
                        if len(pending) >= MAX_CONCURRENT:
                            break
                        if store['id'] in busy:
                            continue
                        fresh = all(now - self.checked.get((p, store['name']), -1e9) < REFRESH_SECONDS for p in parts)
                        if fresh or now - self.attempts.get(store['id'], -1e9) < REFRESH_SECONDS:
                            continue
                        self.attempts[store['id']] = now
                        # A server that returns only a subset must not starve other SKUs.
                        query_parts = sorted(parts, key=lambda p: self.checked.get((p, store['name']), 0))
                        pending[pool.submit(self.client.query, store, query_parts, self.cancelled)] = (store, self.cancelled, now)
                if now - last_publish >= 1:
                    last_publish = now
                    fresh_pairs = sum(now - when < 60 for when in self.checked.values())
                    waiting = max(0, int(retry_at - now))
                    error = state.get('stock_last_error', '')
                    if not enabled:
                        message = '接口监控已暂停，浏览器可自由操作。'
                    elif waiting:
                        message = f'库存查询失败：{error}；{waiting} 秒后重试，失败不记为无货。'
                    else:
                        message = f'接口监控中 · 最近 60 秒覆盖 {fresh_pairs}/{len(parts) * len(STORE_CATALOG)} 个商品/门店组合'
                    self.publish(message, fresh_pairs=fresh_pairs, in_flight=len(pending),
                                 retry_seconds=waiting, elapsed_seconds=round(now - started, 1),
                                 last_request_seconds=self.last_request_seconds)
                self.stop.wait(.2)
        except Exception:
            logging.exception('Stock monitor stopped unexpectedly')
            set_state('worker_seen', 0)
            set_state('worker_message', '库存监控异常退出，请查看 desktop.log 并重启。')
        finally:
            self.cancelled.set()
            pool.shutdown(wait=True, cancel_futures=True)
            self.client.close()
