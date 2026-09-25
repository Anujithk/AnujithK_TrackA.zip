const currency = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' });
const money = n => currency.format(n);
const text = (tag, value, className = '') => {
  const node = document.createElement(tag);
  node.textContent = value;
  node.className = className;
  return node;
};

async function refresh() {
  const status = document.querySelector('#status').value;
  const customer = document.querySelector('#customer') ? document.querySelector('#customer').value : 'all';
  const responses = await Promise.all([
    fetch('/api/overview'),
    fetch(`/api/invoices?status=${encodeURIComponent(status)}&customer=${encodeURIComponent(customer)}`)
  ]);
  if (responses.some(r => !r.ok)) throw new Error('Could not refresh the register.');
  const [data, rows] = await Promise.all(responses.map(r => r.json()));
  document.querySelector('#invoice-count').textContent = data.summary.invoice_count;
  document.querySelector('#open-count').textContent = data.summary.open_count;
  document.querySelector('#outstanding').textContent = money(data.summary.outstanding);
  const body = document.querySelector('#invoices');
  body.replaceChildren();
  rows.forEach(r => {
    const row = document.createElement('tr');
    [r.customer_name, r.invoice_number, r.due_date].forEach(v => row.append(text('td', v)));
    [r.amount, r.paid, r.balance].forEach(v => row.append(text('td', money(v), 'number')));
    row.append(text('td', r.status));
    body.append(row);
  });
  const unmatched = document.querySelector('#unmatched');
  unmatched.replaceChildren(...data.unmatched_payments.map(p => text('li', `${p.payment_id} · ${p.customer_id} / ${p.invoice_number} · ${money(p.amount)}`)));
  if (!data.unmatched_payments.length) unmatched.append(text('li', 'No unmatched payments.'));

  const exportBtn = document.querySelector('a[href^="/api/export"]');
  if (exportBtn) {
    const params = new URLSearchParams();
    if (status !== 'all') params.set('status', status);
    if (customer !== 'all') params.set('customer', customer);
    const qs = params.toString();
    exportBtn.href = `/api/export${qs ? '?' + qs : ''}`;
  }

  document.querySelector('#page-error').textContent = '';
}

async function submitImport(form) {
  const feedback = form.querySelector('.feedback');
  const button = form.querySelector('button');
  const fileInput = form.querySelector('input[type=file]');
  if (!fileInput.files || !fileInput.files.length) {
    feedback.textContent = 'Please choose a CSV file first.';
    return;
  }
  button.disabled = true;
  feedback.textContent = 'Importing…';
  try {
    const csv = await fileInput.files[0].text();
    const res = await fetch(`/api/import?kind=${encodeURIComponent(form.dataset.kind)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'text/csv' },
      body: csv
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = data.error || `Server returned error (${res.status})`;
      feedback.textContent = `Import failed: ${err}`;
      return;
    }
    let msg = `Import complete: ${data.imported} imported, ${data.skipped} skipped, ${data.rejected} rejected.`;
    if (data.errors && data.errors.length > 0) {
      const errorLines = data.errors.map(e => `  Line ${e.line}: ${e.reason}`).join('\n');
      msg += `\nErrors:\n${errorLines}`;
    }
    feedback.textContent = msg;
    fileInput.value = '';
    await refresh();
  } catch (error) {
    feedback.textContent = `Import failed: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

document.querySelector('#status').addEventListener('change', () => refresh().catch(e => { document.querySelector('#page-error').textContent = e.message; }));
const customerSelect = document.querySelector('#customer');
if (customerSelect) {
  customerSelect.addEventListener('change', () => refresh().catch(e => { document.querySelector('#page-error').textContent = e.message; }));
}
document.querySelectorAll('form[data-kind]').forEach(form => form.addEventListener('submit', e => { e.preventDefault(); submitImport(form); }));
refresh().catch(e => { document.querySelector('#page-error').textContent = e.message; });
