# Handover

- Name: Applicant
- Email used for this application: applicant@example.com
- Chosen track: Track A (Repair the register)
- Why this track: I chose Track A to apply rigorous systems investigation, data integrity protection, financial accounting precision, and end-to-end defect remediation to a real-world service business register.
- Approximate total time, including setup and handover: ~150 minutes

## Run and verify

Prerequisites: **Python 3.10+** (tested with Python 3.11.9) and any modern browser. No third-party packages or virtual environments are required; ClearLedger uses only standard library modules.

### Commands

1. **Run full automated test suite (22 unit, regression, and HTTP integration tests):**
   ```text
   py -m unittest discover -s tests -v
   ```
   *(If `py` is not standard on your environment, use `python` or `python3`)*.

2. **Restore and preserve the owner's existing register:**
   ```text
   py restore_fixture.py --replace
   ```
   Output: `Existing register restored: 9 invoices, 5 payments. Start with: python app.py`

3. **Start the application:**
   ```text
   py app.py
   ```
   Open `http://127.0.0.1:8787` in your browser. Verify the dashboard displays:
   - Total invoices: **9**
   - Open invoices: **7**
   - Outstanding: **INR 3,698.19**
   - Unmatched payments: **1** (`KEEP-U1 · MAPLE / WAIT-900 · ₹33.33`)

4. **Reset demo data (optional):**
   ```text
   py app.py reset-demo
   ```
   Output: `Demo reset. Start with: python app.py` (6 invoices, 5 open, INR 3,209.99 outstanding).

---

## What I delivered

I investigated the entire codebase, identified and resolved all **six seeded defects**, preserved the owner's register, and introduced a high-value **Customer Filtering** feature.

### 1. Defect Fixes

- **Defect 1: Open vs Paid Status Filter Inversion** ([reporting.py](file:///ledger/reporting.py#L22)):
  - *Problem*: `requested = {'open': 'paid', 'paid': 'paid'}[status]` caused `status=open` to return paid invoices instead of open invoices.
  - *Fix*: Filter directly by matching status: `result = [r for r in result if r['status'] == status]`.
- **Defect 2: Faulty Payment Matching by Amount** ([matching.py](file:///ledger/matching.py#L4-L6)):
  - *Problem*: `find_invoice()` matched any invoice that happened to share the same amount, regardless of customer ID and invoice reference.
  - *Fix*: Matched strictly by `(customer_id, invoice_number)`. Payments without a matching invoice are retained as unmatched and do not alter balances.
- **Defect 3: Duplicate Invoices & Lack of Import Idempotency** ([storage.py](file:///ledger/storage.py#L61-L82)):
  - *Problem*: `insert_invoice()` blindly inserted rows, duplicating invoices and inflating balances on re-import. Conflicting details were not rejected.
  - *Fix*: Implemented duplicate checking in `insert_invoice` and `insert_payment`. Identical records are skipped without changing totals; conflicting records raise `ValueError` (rejected with original preserved). Added `CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_customer_number` as a database-level integrity safeguard.
- **Defect 4: Row-Level Import Failure Aborting Entire Batch** ([importing.py](file:///ledger/importing.py#L12-L28)):
  - *Problem*: `rows = [normalize(...) for row in reader]` failed eagerly on any invalid row, causing the entire file to abort with HTTP 400.
  - *Fix*: Normalized and imported rows iteratively in a `try...except` block. Invalid data rows reject only that row (logging CSV line number and reason), while valid rows are processed and committed.
- **Defect 5: Floating-Point Truncation in CSV Export** ([reporting.py](file:///ledger/reporting.py#L46-L58)):
  - *Problem*: `int(item[key] * 100) / 100` truncated floating-point values, rendering `19.99` as `19.98` and distorting balances.
  - *Fix*: Used exact two-decimal rounding `round(float(item[key]), 2)` and formatted string decimals `f"{val:.2f}"`, ensuring screen and exported CSV numbers agree.
- **Defect 6: Blind Success Claim & Missing Error Feedback in Web UI** ([app.js](file:///web/app.js#L39-L74)):
  - *Problem*: `submitImport()` ignored HTTP error status and response JSON, unconditionally displaying `"Import complete"` even on 400 errors or rejected rows.
  - *Fix*: Checked `res.ok`, displayed formatted counts (`imported`, `skipped`, `rejected`), listed line-by-line rejection reasons, and refreshed the table.

### 2. Delivered Improvement: Customer Filtering

- *Owner Problem Solved*: The owner needs to prepare for client communications and reconcile balances per client (`HARBOR`, `MAPLE`, `NORTH`). Visually picking rows out of a mixed table was error-prone.
- *Implementation*:
  - Backend: Added `customer` query parameter support to `GET /api/invoices?customer=HARBOR` and `GET /api/export?customer=HARBOR` in [reporting.py](file:///ledger/reporting.py#L5-L29) and [http_app.py](file:///ledger/http_app.py#L35-L44).
  - Frontend: Added interactive `<select id="customer">` in [index.html](file:///web/index.html#L13) and integrated it with `refresh()` in [app.js](file:///web/app.js#L10-L37) so the table and export link reflect the filtered customer instantly.
  - Enriched overview summary with `paid_total`, `unmatched_total`, and `unmatched_count`.
  - Fully tested in [test_regression.py](file:///tests/test_regression.py#L190-L210) and [test_http_integration.py](file:///tests/test_http_integration.py#L97-L105).

---

## Evidence and limits

### 1. Failing-Before / Passing-After Reproductions

- **Reproduction 1: Open Invoice Status Filter**
  - *Command*:
    ```text
    py -c "import sys; sys.path.insert(0, '.'); from ledger import storage, reporting; conn = storage.connect('fixtures/existing-register.sqlite3'); print([r['invoice_number'] for r in reporting.invoices(conn, status='open')])"
    ```
  - *Before Fix*: `['INV-101', 'KEEP-702']` (returned the two **paid** invoices).
  - *After Fix*: `['INV-100', 'INV-200', 'INV-300', 'INV-201', 'INV-301', 'KEEP-700', 'KEEP-700']` (returns all 7 **open** invoices).

- **Reproduction 2: Mixed Row-Level Import**
  - *Command*: Import `samples/invoices-mixed.csv` containing 1 valid row (line 2), 1 invalid row (line 3, amount `not-a-number`), and 1 valid row (line 4).
  - *Before Fix*: Crashed with unhandled `ValueError: amount must be a positive decimal...`, returning HTTP 400 with 0 rows imported.
  - *After Fix*: Returns HTTP 200 with `{"imported": 2, "skipped": 0, "rejected": 1, "errors": [{"line": 3, "reason": "amount must be a positive decimal with at most two decimal places"}]}`. Rows 2 and 4 are safely stored and visible.

- **Reproduction 3: Exported Cents Truncation**
  - *Command*: Inspect `NORTH,INV-300` in exported CSV from `existing-register.sqlite3`.
  - *Before Fix*: `NORTH,INV-300,19.98,10.00,9.98,open` (amount and balance truncated by 1 cent).
  - *After Fix*: `NORTH,INV-300,19.99,10.00,9.99,open` (exact cent precision preserved).

- **Reproduction 4: Payment Amount Misallocation**
  - *Command*: Import payment `P-TEST,NORTH,NONEXISTENT,1250.00`.
  - *Before Fix*: Incorrectly attached payment to `HARBOR,INV-100` because it had amount 1250.00.
  - *After Fix*: Retained as unmatched payment; `HARBOR,INV-100` paid amount remains `0.00`.

### 2. Custom Changed-Input Cases

- **Case A: Overpayment Handling** ([test_overpayment_handling](file:///tests/test_regression.py#L168-L188))
  - Input: Invoice amount `100.00`, payment `150.00`.
  - Observed: Balance is `-50.00`, status is `paid`. Overview outstanding does not deduct the negative balance from other invoices (remains positive sum). Exported CSV outputs `100.00,150.00,-50.00,paid`.
- **Case B: Duplicate and Conflicting Invoices Within & Across Batches** ([test_invoice_import_idempotency_and_conflict](file:///tests/test_regression.py#L75-L105))
  - Re-importing identical invoice `HARBOR,INV-100,1250.00,2026-09-01` yields `skipped: 1`, leaving counts and totals unchanged.
  - Importing same key with changed amount `HARBOR,INV-100,9999.00,2026-09-01` yields `rejected: 1`, leaving the original `1250.00` invoice intact.

### 3. Existing Register Preservation & Restart Check

Verified via [test_existing_register_preservation_and_restart](file:///tests/test_regression.py#L210-L270):
1. Loaded `fixtures/existing-register.sqlite3` and verified exact match against `fixtures/expected-records.json` (9 invoices, 5 payments, 1 unmatched payment `KEEP-U1`, INR 3,698.19 outstanding).
2. Successfully imported a new invoice (`MAPLE / NEW-999`) and payment (`PAY-999`).
3. Closed and reopened database connection (restart simulation). All 10 invoices and 6 payments were preserved with correct balances and allocations.

### 4. Limits and Incomplete Scope

- **Unmatched Payment Rematching**: In accordance with `BUSINESS_RULES.md`, automatic rematching of unmatched payments when a matching invoice is subsequently imported is out of scope.
- **Production Consequential Questions**: For production deployment, I would investigate:
  1. Setting SQLite `PRAGMA journal_mode = WAL` for concurrent reads during file imports.
  2. Introducing an audit log table tracking who imported each file and timestamp.
  3. Adding pagination on `/api/invoices` when records exceed 1,000 items.

---

## Tools and judgment

1. **Storage Integrity: SQLite Constraints vs. Application Logic**
   - *Consideration*: An AI recommendation suggested relying purely on a unique database index to catch duplicates.
   - *Decision & Judgment*: Relying only on SQLite `IntegrityError` inside a batch import would either abort the SQLite transaction or require nested savepoints. I implemented explicit check-before-insert logic in `storage.py` to differentiate identical re-imports (`skipped`) from conflicting records (`rejected`), while still adding `CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_customer_number` as a database safety net.
   - *Verification*: Tested in `test_invoice_import_idempotency_and_conflict` and verified both within-batch and subsequent-run idempotency.

2. **Money Precision: Decimal Class vs. Float Rounding**
   - *Consideration*: Converting the SQLite schema from `REAL` to integer cents or using Python `Decimal` across all models.
   - *Decision & Judgment*: `fixtures/existing-register.sqlite3` uses `REAL` and the assessment requirements mandate preserving the fixture schema and JSON response types (numbers, not strings). Switching storage types would require complex migrations without user-facing benefit for two-decimal currency. I preserved `REAL` in SQLite and enforced `round(float, 2)` at insertion and calculation boundaries, alongside string formatting `f"{val:.2f}"` in CSV export.
   - *Verification*: Tested in `test_money_precision_cents_and_export_csv` with values like `19.99` and `9.99` to ensure complete agreement between screen, database, and CSV export.

3. **Browser Feedback: Silent Failures vs. Actionable Receipts**
   - *Consideration*: The starter frontend swallowed fetch errors and always showed a generic success message.
   - *Decision & Judgment*: Overhauled `submitImport()` to inspect `res.ok`, parse the JSON response, and render a formatted summary of imported, skipped, and rejected counts with line-by-line error messages. Leveraged existing CSS `white-space: pre-line` to format multi-line error lists cleanly.
   - *Verification*: Tested via browser interaction and automated integration tests in `test_http_integration.py`.
