"""GET /app-data/stock-movements (app/routers/stock_feed.py): purchases and sales lines
merged, filtered, paged and balanced in SQL -- the same movements the screen used to
assemble in the browser from the two old endpoints."""
from tests.test_perpetual_inventory import _buy, _company, _save


def _sell(client, headers, no, date, lines, **extra):
    _save(client, headers, "salesInvoices", {"invoice_no": no, "customer": "Cust", "date": date, "status": "Issued",
                                             "lines": lines, **extra})


def _feed(client, headers, **params):
    r = client.get("/api/v1/app-data/stock-movements", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _world(client, db):
    tag, company_id, headers, _ = _company(client, db)
    _buy(client, headers, f"P1-{tag}", 10, 40)                                  # Widget +10 on 2026-10-01
    _buy(client, headers, f"P2-{tag}", 4, 5, product="Bolt", sku="BOLT-1")      # Bolt +4
    _sell(client, headers, f"S1-{tag}", "2026-09-20", [{"description": "Widget", "qty": 3, "unit_price": 100}])
    _sell(client, headers, f"S2-{tag}", "2026-10-05", [{"description": "Widget", "qty": 2, "unit_price": 100},
                                                       {"description": "Bolt", "qty": 1, "unit_price": 9}])
    _sell(client, headers, f"S3-{tag}", "2026-10-06", [{"description": "Widget", "qty": 1}], source="POS")  # POS: skipped
    # Not a stock item: no real movement, so its line becomes a synthetic 'sale' movement.
    _sell(client, headers, f"S4-{tag}", "2026-08-15", [{"description": "Gizmo", "qty": 2, "unit_price": 7}])
    return tag, headers


def test_same_movements_as_the_old_endpoints_without_the_double_count(client, db):
    tag, headers = _world(client, db)
    old_stock = client.get("/api/v1/inventory/stock-movements", headers=headers).json()
    old_sales = client.get("/api/v1/app-data/sales-invoices/stock-movements", headers=headers).json()["movements"]
    # The old screen showed tracked sales twice: the real SALE-<no> movement and the line.
    real_sales = {m["reference"] for m in old_stock if m["reference"].startswith("SALE-")}
    assert real_sales == {f"SALE-S1-{tag}", f"SALE-S2-{tag}"}
    expected = old_stock + [m for m in old_sales if f"SALE-{m['reference']}" not in real_sales]
    new = _feed(client, headers, limit=500)
    key = lambda m: (m["movement_type"], m["item_name"].lower(), round(m["quantity"], 4), m["reference"])  # noqa: E731
    assert sorted(map(key, new["movements"])) == sorted(map(key, expected))
    assert [m["item_name"] for m in new["movements"] if m["movement_type"] == "sale"] == ["Gizmo"]


def test_newest_first_with_running_balance_over_whole_history(client, db):
    tag, headers = _world(client, db)
    rows = _feed(client, headers)["movements"]
    assert [r["date"] for r in rows] == sorted((r["date"] for r in rows), reverse=True)
    widget = [(r["reference"], r["quantity"], r["balance"]) for r in rows if r["item_name"].lower() == "widget"]
    # oldest first, dated by their documents: S1 -3 (09-20) -> -3, P1 +10 (10-01) -> 7, S2 -2 (10-05) -> 5
    assert list(reversed(widget)) == [(f"SALE-S1-{tag}", -3.0, -3.0), (f"P1-{tag}", 10.0, 7.0), (f"SALE-S2-{tag}", -2.0, 5.0)]


def test_item_and_month_filters_keep_the_cumulative_balance(client, db):
    tag, headers = _world(client, db)
    october_widgets = _feed(client, headers, item="WIDGET", month="2026-10")
    assert [(r["reference"], r["balance"]) for r in october_widgets["movements"]] == [(f"SALE-S2-{tag}", 5.0), (f"P1-{tag}", 7.0)]
    assert october_widgets["total"] == 2
    assert _feed(client, headers, month="2026-09")["total"] == 1
    gizmo = _feed(client, headers, item="gizmo")["movements"]
    assert [(r["date"], r["quantity"], r["balance"], r["unit"]) for r in gizmo] == [("2026-08-15", -2.0, -2.0, "PCS")]


def test_paging(client, db):
    _, headers = _world(client, db)
    everything = _feed(client, headers)["movements"]
    first = _feed(client, headers, limit=2, offset=0)
    second = _feed(client, headers, limit=2, offset=2)
    assert first["has_more"] and first["total"] == len(everything)
    assert first["movements"] + second["movements"] == everything[:4]


def test_bad_month_is_rejected(client, db):
    _, headers = _world(client, db)
    r = client.get("/api/v1/app-data/stock-movements", headers=headers, params={"month": "Oct 2026"})
    assert r.status_code == 422
