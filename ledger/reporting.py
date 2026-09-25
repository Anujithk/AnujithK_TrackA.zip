import csv
import io


def invoices(db, status='all', customer='all'):
    if status not in ('all', 'open', 'paid'):
        raise ValueError('status must be all, open or paid')
    valid_customers = {r[0] for r in db.execute('SELECT customer_id FROM customers')}
    if customer != 'all' and customer not in valid_customers:
        raise ValueError(f"Unknown customer '{customer}'")
    data = db.execute('''
        SELECT i.id, i.customer_id, c.name AS customer_name, i.invoice_number,
               i.amount, i.due_date, COALESCE(SUM(p.amount), 0) AS paid
        FROM invoices i JOIN customers c ON c.customer_id=i.customer_id
        LEFT JOIN payments p ON p.invoice_id=i.id
        GROUP BY i.id ORDER BY i.id
    ''').fetchall()
    result = []
    for row in data:
        item = dict(row)
        item['amount'] = round(float(item['amount']), 2)
        item['paid'] = round(float(item['paid']), 2)
        item['balance'] = round(item['amount'] - item['paid'], 2)
        item['status'] = 'paid' if item['balance'] <= 0 else 'open'
        result.append(item)
    if status != 'all':
        result = [r for r in result if r['status'] == status]
    if customer != 'all':
        result = [r for r in result if r['customer_id'] == customer]
    return result


def overview(db):
    rows = invoices(db)
    unmatched = [dict(r) for r in db.execute('''SELECT payment_id, customer_id,
        invoice_number, amount FROM payments WHERE invoice_id IS NULL ORDER BY payment_id''')]
    for p in unmatched:
        p['amount'] = round(float(p['amount']), 2)
    return {'invoices': rows, 'unmatched_payments': unmatched, 'summary': {
        'invoice_count': len(rows),
        'open_count': sum(r['status'] == 'open' for r in rows),
        'outstanding': round(sum(r['balance'] for r in rows if r['status'] == 'open'), 2),
        'paid_total': round(sum(r['paid'] for r in rows), 2),
        'unmatched_total': round(sum(p['amount'] for p in unmatched), 2),
        'unmatched_count': len(unmatched),
    }}


def export_csv(db, status='all', customer='all'):
    output = io.StringIO(newline='')
    fields = ['customer_id', 'invoice_number', 'amount', 'paid', 'balance', 'status']
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in invoices(db, status=status, customer=customer):
        item = {k: row[k] for k in fields}
        for key in ('amount', 'paid', 'balance'):
            val = round(float(item[key]), 2)
            if abs(val) == 0:
                val = 0.0
            item[key] = f"{val:.2f}"
        writer.writerow(item)
    return output.getvalue()

