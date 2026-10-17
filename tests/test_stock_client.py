"""Protocol/scheduler tests with synthetic responses; no Apple requests."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

temporary = tempfile.TemporaryDirectory()
os.environ['DB_PATH'] = str(Path(temporary.name) / 'stock.sqlite3')
os.environ['PICKUP_DATE'] = '2026/10/23'
os.environ['STOCK_MAX_AGE'] = '90'
import web_server as server
import stock_client as stock

PART = server.CATALOG[0]['part']
STORE = server.STORE_CATALOG[0]


def sample(display='available', quote='10 月 23 日可取货'):
    return {'body': {'content': {'pickupMessage': {'stores': [{
        'storeNumber': STORE['id'], 'partsAvailability': {
            PART: {'pickupDisplay': display, 'pickupSearchQuote': quote}}}]}}}}


class ProtocolTests(unittest.TestCase):
    def test_date_and_missing_parts(self):
        rows = stock.parse_stock(sample(), [PART, 'missing'])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0][1]['available'])
        self.assertFalse(stock.parse_stock(sample(quote='10 月 24 日可取货'), [PART])[0][1]['available'])
        self.assertFalse(stock.parse_stock(sample('unavailable'), [PART])[0][1]['available'])

    def test_missing_schema_is_unknown(self):
        for response in ({}, {'body': {'content': {'pickupMessage': {'stores': []}}}}):
            with self.assertRaises(stock.StockError):
                stock.parse_stock(response, [PART])

    def test_batch_url_and_non_json_failure(self):
        class Response:
            status = 200
            def read(self, size):
                return json.dumps(sample()).encode()
        class Connection:
            def request(self, method, path, headers):
                self.path = path
            def getresponse(self):
                return Response()
            def close(self):
                pass
        connection = Connection()
        client = stock.StockClient()
        client.local.connection = connection
        self.assertTrue(client.query(STORE, [PART, 'another'], threading.Event()))
        self.assertIn('parts.1=another', connection.path)
        self.assertIn('store=' + STORE['id'], connection.path)
        with patch.object(Response, 'status', 541):
            with self.assertRaises(stock.StockError) as error:
                client.query(STORE, [PART], threading.Event())
            self.assertEqual(error.exception.status, 541)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        server.initialize()
        with server.connect() as con:
            con.execute('DELETE FROM stock')
        server.set_state('monitoring', True)
        server.set_state('stock_last_error', '')

    def test_success_updates_only_returned_store_and_part(self):
        class Available:
            def query(self, *args):
                return stock.parse_stock(sample(), [PART])
            def close(self):
                pass
        stop = threading.Event()
        monitor = stock.FastStockMonitor(stop, Available())
        monitor.start()
        deadline = time.monotonic() + 2
        while not server.snapshot()['stock'] and time.monotonic() < deadline:
            time.sleep(.05)
        stop.set()
        monitor.join()
        rows = server.snapshot()['stock']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['part'], PART)
        self.assertTrue(rows[0]['available'])
        self.assertEqual(len(monitor.checked), 1)

    def test_blocked_requests_back_off_without_stock(self):
        class Blocked:
            count = 0
            def query(self, *args):
                self.count += 1
                raise stock.StockError('HTTP 541', 541)
            def close(self):
                pass
        client = Blocked()
        stop = threading.Event()
        monitor = stock.FastStockMonitor(stop, client)
        monitor.start()
        time.sleep(1.4)
        stop.set()
        monitor.join()
        self.assertLessEqual(client.count, 2)
        self.assertEqual(server.snapshot()['stock'], [])
        self.assertGreater(server.snapshot()['state']['stock_scan']['retry_seconds'], 0)

    def test_pause_discards_inflight_response(self):
        entered, release = threading.Event(), threading.Event()
        class Slow:
            def query(self, *args):
                entered.set()
                release.wait(3)
                return stock.parse_stock(sample(), [PART])
            def close(self):
                pass
        stop = threading.Event()
        monitor = stock.FastStockMonitor(stop, Slow())
        monitor.start()
        self.assertTrue(entered.wait(2))
        server.set_state('monitoring', False)
        time.sleep(.4)
        release.set()
        time.sleep(.4)
        stop.set()
        monitor.join()
        self.assertEqual(server.snapshot()['stock'], [])


if __name__ == '__main__':
    unittest.main()
