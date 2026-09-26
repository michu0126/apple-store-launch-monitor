"""Loopback dashboard and durable, single-consumer browser work queue."""
import json
import datetime
import os
import re
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('DB_PATH', ROOT / 'dashboard.sqlite3'))
TOKEN = secrets.token_urlsafe(32)
LAUNCH_DATE = os.environ.get('PICKUP_DATE', '2026/09/18')
CATALOG = json.loads((ROOT / 'catalog.json').read_text(encoding='utf-8'))
PRODUCTS = {p['part']: p for p in CATALOG}
STORE_CATALOG = json.loads((ROOT / 'stores.json').read_text(encoding='utf-8'))


def pickup_matches_date(text):
    target = datetime.datetime.strptime(LAUNCH_DATE, '%Y/%m/%d').date()
    explicit = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', text)
    if explicit:
        return tuple(map(int, explicit.groups())) == (target.year, target.month, target.day)
    month_day = re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日', text)
    if month_day:
        return tuple(map(int, month_day.groups())) == (target.month, target.day)
    today = datetime.date.today()
    return ('今日' in text and target == today) or ('明日' in text and target == today + datetime.timedelta(days=1))


def connect():
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def initialize():
    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS stock (part TEXT, store TEXT, available INTEGER,
            pickup TEXT, price REAL, checked REAL, PRIMARY KEY(part,store));
        CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, idem TEXT UNIQUE,
            part TEXT, store TEXT, pickup TEXT, max_price REAL, quantity INTEGER,
            created REAL, updated REAL, status TEXT, message TEXT, order_number TEXT, payment_url TEXT);
        ''')
        for key, value in {'monitoring': True, 'worker_seen': 0, 'worker_message': '等待浏览器监控连接',
                           'account': 'unknown', 'cursor': 0}.items():
            con.execute('INSERT OR IGNORE INTO state VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))


def set_state(key, value):
    with connect() as con:
        con.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))


def get_state(con):
    return {row['key']: json.loads(row['value']) for row in con.execute('SELECT * FROM state')}


def snapshot():
    with connect() as con:
        return dict(catalog=CATALOG, stores=STORE_CATALOG, launch_date=LAUNCH_DATE, stock=[dict(r) for r in con.execute('SELECT * FROM stock')],
                    jobs=[dict(r) for r in con.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 30')],
                    state=get_state(con), now=time.time())


def create_job(body):
    if body.get('confirmed') is not True:
        raise ValueError('请先确认商品、门店、金额和数量。')
    part, store, idem = body.get('part'), body.get('store'), body.get('idempotencyKey')
    if part not in PRODUCTS or not isinstance(store, str) or not isinstance(idem, str) or not 8 <= len(idem) <= 100:
        raise ValueError('下单参数无效。')
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        existing = con.execute('SELECT * FROM jobs WHERE idem=?', (idem,)).fetchone()
        if existing:
            return dict(existing)
        state = get_state(con)
        if time.time() - state.get('worker_seen', 0) > 180:
            raise ValueError('下单执行器离线，请等待重新连接。')
        stock = con.execute('SELECT * FROM stock WHERE part=? AND store=?', (part, store)).fetchone()
        if not stock or not stock['available'] or time.time() - stock['checked'] > 420:
            raise ValueError('库存已过期或不可取货，请等待最新检查。')
        if body.get('price') != stock['price'] or body.get('pickup') != stock['pickup']:
            raise ValueError('价格或取货时间已变化，请重新核对。')
        active = con.execute("SELECT id FROM jobs WHERE status IN ('queued','checking','need_login','need_input','submitting','uncertain','awaiting_payment')").fetchone()
        if active:
            raise ValueError('已有正在处理或待支付的订单，请先处理，避免重复下单。')
        job_id, now = secrets.token_hex(12), time.time()
        con.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (job_id, idem, part, store, stock['pickup'], stock['price'], 1, now, now,
                     'queued', '你已确认下单，等待执行器复核库存；尚未生成 Apple 订单', None, None))
        return dict(con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())


def worker_action(command):
    action = command.get('action')
    if action == 'status':
        with connect() as con:
            return {'state': get_state(con),
                    'jobs': [dict(r) for r in con.execute("SELECT * FROM jobs WHERE status NOT IN ('cancelled','failed','completed') ORDER BY created")],
                    'catalog': CATALOG}
    if action == 'heartbeat':
        set_state('worker_seen', time.time())
        set_state('worker_message', str(command.get('message', '本机监控脚本在线'))[:300])
        if 'cursor' in command:
            set_state('cursor', int(command['cursor']) % len(CATALOG))
        return {'ok': True}
    if action == 'stock':
        part = command.get('part')
        if part not in PRODUCTS:
            raise ValueError('未知商品编号')
        allowed = {store['name'] for store in STORE_CATALOG}
        now = time.time()
        with connect() as con:
            for item in command.get('stores', []):
                store = str(item.get('store', '')).removeprefix('Apple ').strip()
                if store not in allowed or type(item.get('available')) is not bool:
                    continue
                pickup = str(item.get('pickup', ''))[:100]
                on_launch_day = pickup_matches_date(pickup)
                con.execute('INSERT OR REPLACE INTO stock VALUES (?,?,?,?,?,?)',
                            (part, store, int(item['available'] and on_launch_day), pickup,
                             PRODUCTS[part]['price'], now))
        return {'ok': True}
    if action == 'claim':
        with connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute("SELECT id FROM jobs WHERE status IN ('checking','need_input','need_login')").fetchone():
                return {'job': None}
            row = con.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:
                return {'job': None}
            con.execute("UPDATE jobs SET status='checking',updated=?,message='正在 Apple 官网复核商品与门店库存' WHERE id=?", (time.time(), row['id']))
            return {'job': dict(row)}
    if action == 'job':
        status = command.get('status')
        if status not in {'need_login', 'need_input', 'failed', 'checking', 'completed'}:
            raise ValueError('无效下单状态')
        with connect() as con:
            changed = con.execute("UPDATE jobs SET status=?,message=?,updated=? WHERE id=? AND status IN ('checking','need_login','need_input')",
                                  (status, str(command.get('message', ''))[:500], time.time(), command.get('id'))).rowcount
            if not changed:
                raise ValueError('下单请求状态已变化')
        return {'ok': True}
    raise ValueError('未知执行器操作')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, data, mime='application/json; charset=utf-8'):
        payload = json.dumps(data, ensure_ascii=False).encode() if mime.startswith('application/json') else data
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def valid_host(self):
        host = self.headers.get('Host', '').strip()
        try:
            parsed = urlsplit('http://' + host)
            port = parsed.port
        except ValueError:
            return False
        return bool(parsed.hostname) and parsed.username is None and parsed.password is None and \
            not parsed.path and not parsed.query and not parsed.fragment and \
            (port is None or 1 <= port <= 65535)

    def valid_origin(self):
        if not self.valid_host():
            return False
        origin = self.headers.get('Origin', '')
        if not origin:
            referer = urlsplit(self.headers.get('Referer', ''))
            origin = referer.scheme + '://' + referer.netloc
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return False
        return parsed.scheme in {'http', 'https'} and parsed.netloc.lower() == self.headers.get('Host', '').lower() and \
            not parsed.path.rstrip('/') and not parsed.query and not parsed.fragment

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {'error': 'Invalid host'})
        path = urlsplit(self.path).path
        if path == '/api/state':
            return self.reply(200, snapshot())
        if path == '/api/session':
            return self.reply(200, {'token': TOKEN})
        files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path in files:
            file, mime = files[path]
            return self.reply(200, (ROOT / 'web' / file).read_bytes(), mime)
        return self.reply(404, {'error': 'Not found'})

    def do_POST(self):
        if (not self.valid_origin() or
                not self.headers.get('Content-Type', '').startswith('application/json')):
            return self.reply(403, {'error': '请求验证失败，请从本机监控页面操作。'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < 8192:
                raise ValueError('请求大小无效')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('请求无效')
            if self.path == '/api/orders':
                return self.reply(200, create_job(body))
            if self.path == '/api/monitor':
                if type(body.get('enabled')) is not bool:
                    raise ValueError('监控参数无效')
                set_state('monitoring', body['enabled'])
                return self.reply(200, {'ok': True})
            if self.path == '/api/refresh':
                set_state('refresh_requested', time.time())
                return self.reply(200, {'ok': True, 'message': '已请求执行器优先检查；不会把旧数据更新成新库存。'})
            if self.path == '/api/account':
                set_state('monitoring', False)
                set_state('account_requested', time.time())
                return self.reply(200, {'ok': True})
            if self.path == '/api/cancel':
                with connect() as con:
                    changed = con.execute("UPDATE jobs SET status='cancelled', message='已取消尚未执行的下单请求', updated=? WHERE id=? AND status='queued'", (time.time(), body.get('id'))).rowcount
                    if not changed:
                        raise ValueError('请求已开始处理，不能在这里撤回。请查看当前状态。')
                return self.reply(200, {'ok': True})
            return self.reply(404, {'error': 'Not found'})
        except (ValueError, TypeError) as error:
            return self.reply(409, {'error': str(error)})


if __name__ == '__main__':
    initialize()
    print('Apple dashboard: http://127.0.0.1:8765', flush=True)
    ThreadingHTTPServer((os.environ.get('MONITOR_BIND', '127.0.0.1'), 8765), Handler).serve_forever()
