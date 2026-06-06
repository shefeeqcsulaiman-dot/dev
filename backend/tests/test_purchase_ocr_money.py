from decimal import Decimal

from app.routers.app_data import money_values_in_line, parse_columnar_purchase_item


def test_money_values_in_line_splits_glued_decimal_amounts():
    assert money_values_in_line("13.54284.34") == ["13.54", "284.34"]
    assert money_values_in_line("60.1. 1,262.00") == ["60.1", "1262.00"]
    assert money_values_in_line("AED 3,257.40") == ["3257.40"]


def test_columnar_purchase_item_keeps_values_after_decimal_when_ocr_glues_amounts():
    row = parse_columnar_purchase_item(
        ["7 [E000643] Catalyst Sample Cups 450pcs 1 270.80 0.00 270.8 270.8 13.54284.34"],
        "INV/2026/02964",
        "03/04/2026",
        "Eurovets Veterinary Medicines L.L.C.",
        "100365304300003",
    )

    assert row is not None
    assert Decimal(str(row["vat_amount"])) == Decimal("13.54")
    assert Decimal(str(row["line_total"])) == Decimal("270.8")


def test_columnar_purchase_item_reads_qty_from_vcode_column_not_pack_size():
    row = parse_columnar_purchase_item(
        ["[E000568] Catalyst Whole Blood Sample 98- 5 Pipette Tips - 50's 17321-00 1 EV060126 42.00 0.00 42.0 42.0 2.1 44.10"],
        "INV/2026/02964",
        "03/04/2026",
        "Eurovets Veterinary Medicines L.L.C.",
        "100365304300003",
    )

    assert row is not None
    assert Decimal(str(row["quantity"])) == Decimal("1")
    assert Decimal(str(row["line_total"])) == Decimal("42.0")
    assert Decimal(str(row["vat_amount"])) == Decimal("2.1")


def test_columnar_purchase_item_calculates_line_total_when_net_column_is_missing():
    row = parse_columnar_purchase_item(
        ["[E000632] Catalyst Alt Alanine Aminotransferase (12) 11067-01 = 2 15/09/2027 208.90 20.80 438.80"],
        "INV/2026/02964",
        "03/04/2026",
        "Eurovets Veterinary Medicines L.L.C.",
        "100365304300003",
    )

    assert row is not None
    assert Decimal(str(row["quantity"])) == Decimal("2")
    assert Decimal(str(row["line_total"])) == Decimal("418.00")
    assert Decimal(str(row["vat_amount"])) == Decimal("20.80")
