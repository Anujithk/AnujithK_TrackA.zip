"""HTTP Integration tests for ClearLedger web server."""
import json
import socket
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path

from ledger import storage
from ledger.http_app import make_server

ROOT = Path(__file__).resolve().parent.parent


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class HttpIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tmp.name) / 'http_test.sqlite3'
        cls.port = get_free_port()
        cls.server = make_server(cls.db_path, ROOT / 'web', cls.port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f'http://127.0.0.1:{cls.port}'

    def setUp(self):
        # Reset database to clean demo state for each test
        if self.db_path.exists():
            self.db_path.unlink()
        db = storage.connect(self.db_path)
        storage.seed(db)
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()


    def request(self, path, method='GET', data=None, headers=None):
        url = f'{self.base_url}{path}'
        req_headers = headers or {}
        body = data.encode('utf-8') if isinstance(data, str) else data
        req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.headers, resp.read().decode('utf-8')
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read().decode('utf-8')

    def test_static_files(self):
        status, _, body = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn('<title>ClearLedger</title>', body)
        self.assertIn('id="customer"', body)

        status, _, body = self.request('/app.js')
        self.assertEqual(status, 200)
        self.assertIn('submitImport', body)

        status, _, body = self.request('/style.css')
        self.assertEqual(status, 200)

    def test_overview_endpoint(self):
        status, _, body = self.request('/api/overview')
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn('summary', data)
        self.assertIn('invoices', data)
        self.assertIn('unmatched_payments', data)
        self.assertEqual(data['summary']['invoice_count'], 6)
        self.assertEqual(data['summary']['open_count'], 5)
        self.assertEqual(data['summary']['outstanding'], 3209.99)

    def test_invoices_status_filter_endpoint(self):
        status, _, body = self.request('/api/invoices?status=open')
        self.assertEqual(status, 200)
        invoices = json.loads(body)
        self.assertEqual(len(invoices), 5)
        self.assertTrue(all(r['status'] == 'open' for r in invoices))

        status, _, body = self.request('/api/invoices?status=paid')
        self.assertEqual(status, 200)
        paid = json.loads(body)
        self.assertEqual(len(paid), 1)
        self.assertEqual(paid[0]['invoice_number'], 'INV-101')

        status, _, body = self.request('/api/invoices?status=invalid')
        self.assertEqual(status, 400)
        err = json.loads(body)
        self.assertIn('status must be all, open or paid', err['error'])

    def test_invoices_customer_filter_endpoint(self):
        status, _, body = self.request('/api/invoices?customer=HARBOR')
        self.assertEqual(status, 200)
        invoices = json.loads(body)
        self.assertEqual(len(invoices), 2)
        self.assertTrue(all(r['customer_id'] == 'HARBOR' for r in invoices))

        status, _, body = self.request('/api/invoices?customer=UNKNOWN')
        self.assertEqual(status, 400)

    def test_export_endpoint(self):
        status, headers, body = self.request('/api/export')
        self.assertEqual(status, 200)
        self.assertIn('text/csv', headers.get('Content-Type'))
        self.assertTrue(body.startswith('customer_id,invoice_number,amount,paid,balance,status'))
        self.assertIn('NORTH,INV-300,19.99,10.00,9.99,open', body)

    def test_import_mixed_invoices(self):
        csv_data = "customer_id,invoice_number,amount,due_date\nHARBOR,HTTP-1,50.00,2026-09-15\nNORTH,HTTP-2,invalid-amt,2026-09-15\nMAPLE,HTTP-3,75.00,2026-09-16\n"
        headers = {'Content-Type': 'text/csv', 'Origin': f'http://127.0.0.1:{self.port}'}
        status, _, body = self.request('/api/import?kind=invoices', method='POST', data=csv_data, headers=headers)
        self.assertEqual(status, 200)
        res = json.loads(body)
        self.assertEqual(res['imported'], 2)
        self.assertEqual(res['skipped'], 0)
        self.assertEqual(res['rejected'], 1)
        self.assertEqual(len(res['errors']), 1)
        self.assertEqual(res['errors'][0]['line'], 3)

    def test_import_wrong_header(self):
        csv_data = "customer,invoice,val\nHARBOR,INV-1,50.00\n"
        headers = {'Content-Type': 'text/csv', 'Origin': f'http://127.0.0.1:{self.port}'}
        status, _, body = self.request('/api/import?kind=invoices', method='POST', data=csv_data, headers=headers)
        self.assertEqual(status, 400)
        res = json.loads(body)
        self.assertIn('Expected CSV header', res['error'])


if __name__ == '__main__':
    unittest.main()
