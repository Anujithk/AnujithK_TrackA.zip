"""Comprehensive regression tests covering defect fixes, business rules,
small improvements, and fixture preservation.
"""
import csv
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ledger import storage, reporting, importing, validation


ROOT = Path(__file__).resolve().parent.parent


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'test.sqlite3'
        self.db = storage.connect(self.db_path)
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_reporting_status_filter_open_and_paid(self):
        """Defect 1: status='open' must return open invoices only, not paid."""
        open_invoices = reporting.invoices(self.db, status='open')
        paid_invoices = reporting.invoices(self.db, status='paid')
        all_invoices = reporting.invoices(self.db, status='all')

        self.assertEqual(len(all_invoices), 6)
        self.assertEqual(len(open_invoices), 5)
        self.assertEqual(len(paid_invoices), 1)

        # In seed data, INV-101 is paid (300.00 / 300.00 paid, balance 0.00)
        self.assertEqual(paid_invoices[0]['invoice_number'], 'INV-101')
        self.assertEqual(paid_invoices[0]['status'], 'paid')

        for inv in open_invoices:
            self.assertEqual(inv['status'], 'open')
            self.assertGreater(inv['balance'], 0)

        # Invalid status must raise ValueError
        with self.assertRaises(ValueError):
            reporting.invoices(self.db, status='pending')

    def test_matching_by_customer_and_invoice_not_by_amount(self):
        """Defect 2: Payment must match by (customer_id, invoice_number), never by amount alone."""
        # HARBOR / INV-100 has amount 1250.00.
        # Import a payment with amount 1250.00 for NORTH / NONEXISTENT.
        csv_text = "payment_id,customer_id,invoice_number,amount\nP-WRONG,NORTH,NONEXISTENT,1250.00\n"
        res = importing.import_csv(self.db, csv_text, 'payments')
        self.assertEqual(res['imported'], 1)

        # HARBOR / INV-100 must remain unpaid (paid = 0.00)
        inv_100 = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-100')
        self.assertEqual(inv_100['paid'], 0.00)
        self.assertEqual(inv_100['balance'], 1250.00)

        # The payment must be listed in unmatched_payments
        ov = reporting.overview(self.db)
        unmatched_ids = [p['payment_id'] for p in ov['unmatched_payments']]
        self.assertIn('P-WRONG', unmatched_ids)

        # A payment with matching customer and invoice must attach correctly
        match_csv = "payment_id,customer_id,invoice_number,amount\nP-MATCH,HARBOR,INV-100,250.00\n"
        res2 = importing.import_csv(self.db, match_csv, 'payments')
        self.assertEqual(res2['imported'], 1)
        inv_100_after = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-100')
        self.assertEqual(inv_100_after['paid'], 250.00)
        self.assertEqual(inv_100_after['balance'], 1000.00)

    def test_invoice_import_idempotency_and_conflict(self):
        """Defect 3: Re-importing identical invoice skips it; different details reject it."""
        # Identical re-import of HARBOR INV-100 (amount 1250.00, due 2026-09-01)
        csv_identical = "customer_id,invoice_number,amount,due_date\nHARBOR,INV-100,1250.00,2026-09-01\n"
        res1 = importing.import_csv(self.db, csv_identical, 'invoices')
        self.assertEqual(res1, {'imported': 0, 'skipped': 1, 'rejected': 0, 'errors': []})
        self.assertEqual(len(reporting.invoices(self.db)), 6)
        self.assertEqual(reporting.overview(self.db)['summary']['outstanding'], 3209.99)

        # Re-import with changed amount -> rejected, original preserved
        csv_conflict_amt = "customer_id,invoice_number,amount,due_date\nHARBOR,INV-100,9999.00,2026-09-01\n"
        res2 = importing.import_csv(self.db, csv_conflict_amt, 'invoices')
        self.assertEqual(res2['rejected'], 1)
        self.assertEqual(res2['errors'][0]['line'], 2)
        inv_100 = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-100')
        self.assertEqual(inv_100['amount'], 1250.00)

        # Re-import with changed due date -> rejected, original preserved
        csv_conflict_date = "customer_id,invoice_number,amount,due_date\nHARBOR,INV-100,1250.00,2026-12-31\n"
        res3 = importing.import_csv(self.db, csv_conflict_date, 'invoices')
        self.assertEqual(res3['rejected'], 1)
        self.assertEqual(res3['errors'][0]['line'], 2)
        inv_100 = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-100')
        self.assertEqual(inv_100['due_date'], '2026-09-01')

    def test_payment_import_idempotency_and_conflict(self):
        """Defect 3 (Payments): Re-importing identical payment skips; conflicting details reject."""
        # Identical re-import of SEED-1 (HARBOR, INV-101, 300.00)
        csv_identical = "payment_id,customer_id,invoice_number,amount\nSEED-1,HARBOR,INV-101,300.00\n"
        res1 = importing.import_csv(self.db, csv_identical, 'payments')
        self.assertEqual(res1, {'imported': 0, 'skipped': 1, 'rejected': 0, 'errors': []})

        # Conflicting amount on SEED-1
        csv_conflict_amt = "payment_id,customer_id,invoice_number,amount\nSEED-1,HARBOR,INV-101,500.00\n"
        res2 = importing.import_csv(self.db, csv_conflict_amt, 'payments')
        self.assertEqual(res2['rejected'], 1)
        self.assertEqual(res2['errors'][0]['line'], 2)

        # Conflicting customer on SEED-1
        csv_conflict_cust = "payment_id,customer_id,invoice_number,amount\nSEED-1,MAPLE,INV-101,300.00\n"
        res3 = importing.import_csv(self.db, csv_conflict_cust, 'payments')
        self.assertEqual(res3['rejected'], 1)

    def test_import_row_level_validation_and_partial_success(self):
        """Defect 4: Invalid data rows reject only that row; valid rows are processed."""
        # Using samples/invoices-mixed.csv
        mixed_csv = (ROOT / 'samples' / 'invoices-mixed.csv').read_text(encoding='utf-8')
        res = importing.import_csv(self.db, mixed_csv, 'invoices')

        self.assertEqual(res['imported'], 2)
        self.assertEqual(res['skipped'], 0)
        self.assertEqual(res['rejected'], 1)
        self.assertEqual(len(res['errors']), 1)
        self.assertEqual(res['errors'][0]['line'], 3)
        self.assertIn('amount', res['errors'][0]['reason'])

        # Verify that row 2 (HARBOR, INV-103) and row 4 (MAPLE, INV-203) were imported
        invoices = {r['invoice_number']: r for r in reporting.invoices(self.db)}
        self.assertIn('INV-103', invoices)
        self.assertIn('INV-203', invoices)
        self.assertNotIn('INV-302', invoices)

    def test_empty_rows_and_invalid_header(self):
        """Business rules: Valid header with no data rows succeeds with 0 counts; bad header rejects."""
        empty_csv = "customer_id,invoice_number,amount,due_date\n"
        res = importing.import_csv(self.db, empty_csv, 'invoices')
        self.assertEqual(res, {'imported': 0, 'skipped': 0, 'rejected': 0, 'errors': []})

        bad_header_csv = (ROOT / 'samples' / 'wrong-header.csv').read_text(encoding='utf-8')
        with self.assertRaises(ValueError):
            importing.import_csv(self.db, bad_header_csv, 'invoices')

    def test_money_precision_cents_and_export_csv(self):
        """Defect 5: Exported CSV must preserve exact cents and agree with screen values."""
        # NORTH INV-300 has amount 19.99, payment 10.00, balance 9.99
        csv_out = reporting.export_csv(self.db)
        reader = list(csv.DictReader(io.StringIO(csv_out)))

        row_300 = next(r for r in reader if r['invoice_number'] == 'INV-300')
        self.assertEqual(row_300['amount'], '19.99')
        self.assertEqual(row_300['paid'], '10.00')
        self.assertEqual(row_300['balance'], '9.99')
        self.assertEqual(row_300['status'], 'open')

    def test_overpayment_handling(self):
        """Business rules: Overpayments show negative balance, marked paid, don't reduce others."""
        # Overpay INV-301 (amount 100.00) with payment 150.00
        pay_csv = "payment_id,customer_id,invoice_number,amount\nPAY-OVER,NORTH,INV-301,150.00\n"
        res = importing.import_csv(self.db, pay_csv, 'payments')
        self.assertEqual(res['imported'], 1)

        inv_301 = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-301')
        self.assertEqual(inv_301['amount'], 100.00)
        self.assertEqual(inv_301['paid'], 150.00)
        self.assertEqual(inv_301['balance'], -50.00)
        self.assertEqual(inv_301['status'], 'paid')

        # Outstanding should exclude negative balance (only sum of positive balances)
        ov = reporting.overview(self.db)
        # Previous outstanding was 3209.99. INV-301 had balance 100.00.
        # Now INV-301 has -50.00, so outstanding is 3209.99 - 100.00 = 3109.99
        self.assertEqual(ov['summary']['outstanding'], 3109.99)

        # Export must show negative balance formatted correctly
        csv_out = reporting.export_csv(self.db)
        row_301 = next(r for r in csv.DictReader(io.StringIO(csv_out)) if r['invoice_number'] == 'INV-301')
        self.assertEqual(row_301['balance'], '-50.00')
        self.assertEqual(row_301['status'], 'paid')

    def test_customer_filter_improvement(self):
        """Improvement: Filter invoices and exports by customer_id."""
        harbor_invoices = reporting.invoices(self.db, customer='HARBOR')
        self.assertTrue(all(r['customer_id'] == 'HARBOR' for r in harbor_invoices))
        self.assertEqual(len(harbor_invoices), 2)  # INV-100, INV-101

        # Combined filter: customer + status
        harbor_open = reporting.invoices(self.db, status='open', customer='HARBOR')
        self.assertEqual(len(harbor_open), 1)
        self.assertEqual(harbor_open[0]['invoice_number'], 'INV-100')

        # Export with customer filter
        csv_harbor = reporting.export_csv(self.db, customer='HARBOR')
        rows = list(csv.DictReader(io.StringIO(csv_harbor)))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['customer_id'] == 'HARBOR' for r in rows))

        # Unknown customer raises ValueError
        with self.assertRaises(ValueError):
            reporting.invoices(self.db, customer='INVALID')

    def test_existing_register_preservation_and_restart(self):
        """Verify owner's register preservation, new imports, and restart resilience."""
        fixture_src = ROOT / 'fixtures' / 'existing-register.sqlite3'
        working_db = Path(self.tmp.name) / 'existing_test.sqlite3'
        shutil.copy2(fixture_src, working_db)

        # Load reference
        expected = json.loads((ROOT / 'fixtures' / 'expected-records.json').read_text(encoding='utf-8'))

        # 1. Connect and verify expected records
        db = storage.connect(working_db)
        try:
            storage.seed(db)  # Should not overwrite existing records

            ov = reporting.overview(db)
            self.assertEqual(ov['summary']['invoice_count'], expected['summary']['invoice_count'])
            self.assertEqual(ov['summary']['open_count'], expected['summary']['open_count'])
            self.assertEqual(ov['summary']['outstanding'], float(expected['summary']['outstanding']))
            self.assertEqual(len(ov['unmatched_payments']), 1)
            self.assertEqual(ov['unmatched_payments'][0]['payment_id'], 'KEEP-U1')
            self.assertEqual(ov['unmatched_payments'][0]['amount'], 33.33)

            # Key by (customer_id, invoice_number) since (customer, number) defines identity
            invoices_by_key = {(r['customer_id'], r['invoice_number']): r for r in reporting.invoices(db)}
            for exp in expected['invoices']:
                key = (exp['customer_id'], exp['invoice_number'])
                self.assertIn(key, invoices_by_key)
                self.assertEqual(invoices_by_key[key]['amount'], float(exp['amount']))
                self.assertEqual(invoices_by_key[key]['due_date'], exp['due_date'])

            # 2. Import a new invoice and a new payment
            new_inv_csv = "customer_id,invoice_number,amount,due_date\nMAPLE,NEW-999,500.00,2026-09-20\n"
            res_inv = importing.import_csv(db, new_inv_csv, 'invoices')
            self.assertEqual(res_inv['imported'], 1)

            new_pay_csv = "payment_id,customer_id,invoice_number,amount\nPAY-999,MAPLE,NEW-999,200.00\n"
            res_pay = importing.import_csv(db, new_pay_csv, 'payments')
            self.assertEqual(res_pay['imported'], 1)
        finally:
            db.close()

        # 3. Simulate application restart: close and reopen connection
        db_restarted = storage.connect(working_db)
        try:
            storage.seed(db_restarted)

            # Verify all original 9 + 1 new = 10 invoices exist
            restarted_invoices = reporting.invoices(db_restarted)
            self.assertEqual(len(restarted_invoices), 10)

            # Verify new invoice balance
            new_inv = next(r for r in restarted_invoices if r['invoice_number'] == 'NEW-999')
            self.assertEqual(new_inv['amount'], 500.00)
            self.assertEqual(new_inv['paid'], 200.00)
            self.assertEqual(new_inv['balance'], 300.00)
            self.assertEqual(new_inv['status'], 'open')

            # Verify KEEP-700 records remain intact for both HARBOR and MAPLE
            harbor_keep = next(r for r in restarted_invoices if r['customer_id'] == 'HARBOR' and r['invoice_number'] == 'KEEP-700')
            self.assertEqual(harbor_keep['paid'], 56.78)
            self.assertEqual(harbor_keep['balance'], 400.00)

            maple_keep = next(r for r in restarted_invoices if r['customer_id'] == 'MAPLE' and r['invoice_number'] == 'KEEP-700')
            self.assertEqual(maple_keep['paid'], 0.00)
            self.assertEqual(maple_keep['balance'], 88.20)
        finally:
            db_restarted.close()


if __name__ == '__main__':
    unittest.main()
