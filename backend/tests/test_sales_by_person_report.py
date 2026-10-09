"""Sales by Sales Person report (/reports/sales-by-person)."""
import uuid


def _save(client, headers, collection, record):
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "save"},
                    json={"collection": collection, "record": record})
    assert r.status_code == 200, r.text


def _inv(no, person, total, vat, status="Pending", date="2031-03-10", doc="Sales Invoice"):
    return {"invoice_no": no, "customer": "Report Test Customer", "date": date, "salesperson": person,
            "subtotal": total - vat, "vat_amount": vat, "total": total, "status": status, "document_type": doc,
            "lines": [{"description": "x", "qty": 1, "price": total - vat, "amount": total - vat}]}


def test_sales_by_person_totals_returns_and_exclusions(client, auth_headers):
    tag = uuid.uuid4().hex[:6]
    alice, bob, idle = f"Alice {tag}", f"Bob {tag}", f"Idle {tag}"
    for name in (alice, bob, idle):
        _save(client, auth_headers, "salesPeople", {"id": f"SP-{name}", "name": name, "status": "Active"})
    _save(client, auth_headers, "salesInvoices", _inv(f"R1-{tag}", alice, 105, 5))
    _save(client, auth_headers, "salesInvoices", _inv(f"R2-{tag}", alice, 210, 10))
    _save(client, auth_headers, "salesInvoices", _inv(f"R3-{tag}", alice.upper(), 52.5, 2.5, doc="Sales Return", status="Return"))
    _save(client, auth_headers, "salesInvoices", _inv(f"R4-{tag}", bob, 420, 20))
    _save(client, auth_headers, "salesInvoices", _inv(f"R5-{tag}", bob, 999, 0, status="Draft"))
    _save(client, auth_headers, "salesInvoices", _inv(f"R6-{tag}", bob, 999, 0, status="Cancelled"))
    _save(client, auth_headers, "salesInvoices", _inv(f"R7-{tag}", bob, 999, 0, date="2031-05-01"))   # outside range

    r = client.get("/api/v1/reports/sales-by-person", headers=auth_headers,
                   params={"date_from": "2031-03-01", "date_to": "2031-03-31"})
    assert r.status_code == 200, r.text
    rows = {row["name"]: row for row in r.json()["rows"]}

    a = rows[alice]
    assert (a["invoices"], a["returns"]) == (2, 1)
    assert a["gross_sales"] == "315.00" and a["returns_total"] == "52.50" and a["net_total"] == "262.50"
    assert a["net_sales"] == "300.00" and a["vat"] == "15.00" and a["average_invoice"] == "157.50"

    b = rows[bob]
    assert (b["invoices"], b["net_total"]) == (1, "420.00")          # draft, cancelled, out-of-range ignored

    assert rows[idle]["invoices"] == 0 and rows[idle]["net_total"] == "0.00"   # listed even with no sales
    assert list(rows).index(bob) < list(rows).index(alice)                       # highest first


def test_sales_by_person_rejects_bad_dates(client, auth_headers):
    r = client.get("/api/v1/reports/sales-by-person", headers=auth_headers, params={"date_from": "01-03-2031"})
    assert r.status_code == 400
