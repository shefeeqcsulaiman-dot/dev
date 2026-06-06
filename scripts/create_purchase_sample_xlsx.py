from __future__ import annotations

from datetime import date
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


OUTPUT = Path("outputs/purchase_upload_10_records.xlsx")


headers = [
    "Invoice No.",
    "Purchase Date",
    "Supplier",
    "Address",
    "Pay Term",
    "Product Name",
    "Category",
    "Purchase Quantity",
    "Unit of Measure",
    "Unit Cost Before Discount",
    "Discount %",
    "Unit Cost Before Tax",
    "Line Total",
    "Profit Margin %",
    "Unit Selling Price Inc. Tax",
    "Discount Type",
    "Discount Amount",
    "Purchase Tax",
    "Shipping Details",
    "Shipping Charges",
    "Paid Amount",
    "Paid On",
    "Payment Method",
    "Payment Account",
    "Payment Note",
    "Notes",
    "Payment Due",
]


rows = [
    ["PUR-2026-0001", date(2026, 5, 1), "Gulf Fresh Foods", "Al Quoz, Dubai", "Net 15", "Basmati Rice 25kg", "Food Staples", 12, "BAG", 78.00, 2, None, None, 18, None, "None", 0, "VAT 5%", "Store delivery", 35, 500, date(2026, 5, 1), "Bank Transfer", "Emirates NBD", "Advance paid", "Monthly stock purchase", None],
    ["PUR-2026-0002", date(2026, 5, 2), "Al Noor Packaging", "Industrial Area, Sharjah", "Net 30", "Paper Cups 250ml", "Packaging", 40, "CTN", 22.50, 0, None, None, 25, None, "Fixed", 20, "VAT 5%", "Courier", 15, 0, "", "Cash", "None", "", "For cafe counter", None],
    ["PUR-2026-0003", date(2026, 5, 3), "Dubai Cleaning Supply", "Deira, Dubai", "Due on receipt", "Floor Cleaner 5L", "Cleaning", 18, "LTR", 16.75, 5, None, None, 20, None, "None", 0, "VAT 5%", "Supplier van", 0, 316.00, date(2026, 5, 3), "Card", "FAB", "Paid at counter", "Housekeeping supplies", None],
    ["PUR-2026-0004", date(2026, 5, 4), "Tech Office UAE", "Business Bay, Dubai", "Net 15", "Barcode Labels Roll", "Office Supplies", 25, "ROLL", 9.80, 0, None, None, 30, None, "Percentage", 3, "VAT 5%", "Standard delivery", 20, 0, "", "Bank Transfer", "Emirates NBD", "", "Inventory labels", None],
    ["PUR-2026-0005", date(2026, 5, 5), "Prime Dairy Trading", "Ras Al Khor, Dubai", "Net 15", "Fresh Milk 1L", "Dairy", 90, "PCS", 4.20, 1, None, None, 12, None, "None", 0, "VAT 5%", "Cold chain", 45, 250, date(2026, 5, 5), "Online", "FAB", "Partial payment", "Daily dairy supply", None],
    ["PUR-2026-0006", date(2026, 5, 6), "Green Farm Produce", "Al Ain", "Net 30", "Tomato Premium Box", "Vegetables", 30, "BOX", 18.00, 0, None, None, 22, None, "Fixed", 10, "VAT 5%", "Refrigerated truck", 60, 0, "", "Cheque", "None", "Cheque pending", "Fresh produce", None],
    ["PUR-2026-0007", date(2026, 5, 7), "Emirates Hardware", "Mussafah, Abu Dhabi", "Net 45", "Stainless Steel Shelf", "Fixtures", 6, "PCS", 145.00, 4, None, None, 35, None, "None", 0, "VAT 5%", "Heavy item delivery", 80, 500, date(2026, 5, 8), "Bank Transfer", "Emirates NBD", "Deposit paid", "Store fixture upgrade", None],
    ["PUR-2026-0008", date(2026, 5, 8), "Royal Beverage LLC", "Jebel Ali, Dubai", "Net 15", "Mineral Water 500ml", "Beverages", 75, "CTN", 11.50, 0, None, None, 16, None, "Percentage", 2, "VAT 5%", "Warehouse delivery", 25, 400, date(2026, 5, 8), "Card", "FAB", "", "Beverage restock", None],
    ["PUR-2026-0009", date(2026, 5, 9), "City Uniforms", "Karama, Dubai", "Net 30", "Staff Apron Black", "Uniforms", 20, "PCS", 28.00, 3, None, None, 28, None, "None", 0, "VAT 5%", "Pickup", 0, 0, "", "Cash", "None", "", "New employee uniforms", None],
    ["PUR-2026-0010", date(2026, 5, 10), "Alpha Electronics", "Dubai Silicon Oasis", "Net 30", "Thermal Receipt Printer", "Equipment", 3, "PCS", 420.00, 0, None, None, 32, None, "Fixed", 50, "VAT 5%", "Fragile delivery", 30, 1000, date(2026, 5, 10), "Bank Transfer", "Emirates NBD", "Balance after delivery", "POS hardware", None],
]


def col_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def cell_xml(row: int, col: int, value) -> str:
    ref = f"{col_name(col)}{row}"
    if value is None:
        return ""
    if isinstance(value, date):
        serial = (value - date(1899, 12, 30)).days
        return f'<c r="{ref}" s="2"><v>{serial}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}" s="3"><v>{value}</v></c>'
    escaped = (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return f'<c r="{ref}" t="inlineStr"><is><t>{escaped}</t></is></c>'


def formula_cell(row: int, col: int, formula: str, style: int = 3) -> str:
    ref = f"{col_name(col)}{row}"
    return f'<c r="{ref}" s="{style}"><f>{formula}</f></c>'


def make_sheet_xml() -> str:
    xml_rows = []
    xml_rows.append(
        '<row r="1">'
        '<c r="A1" t="inlineStr" s="4"><is><t>Purchase Upload Sample - 10 Records</t></is></c>'
        "</row>"
    )
    xml_rows.append(
        '<row r="2">'
        '<c r="A2" t="inlineStr" s="5"><is><t>Upload this .xlsx in Purchases &gt; Upload, then run AI Extract.</t></is></c>'
        "</row>"
    )
    xml_rows.append("<row r=\"4\">" + "".join(cell_xml(4, i, h) for i, h in enumerate(headers)) + "</row>")
    for offset, row_values in enumerate(rows, start=5):
        cells = []
        for col, value in enumerate(row_values):
            if col in {11, 12, 14, 26}:
                continue
            cells.append(cell_xml(offset, col, value))
        cells.append(formula_cell(offset, 11, f"J{offset}*(1-K{offset}/100)"))
        cells.append(formula_cell(offset, 12, f"H{offset}*L{offset}"))
        cells.append(formula_cell(offset, 14, f"L{offset}*(1+N{offset}/100)*1.05"))
        cells.append(formula_cell(offset, 26, f"MAX(0,(M{offset}-Q{offset})+R{offset}+T{offset}-U{offset})"))
        xml_rows.append(f'<row r="{offset}">' + "".join(cells) + "</row>")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<dimension ref=\"A1:AA14\"/>"
        "<sheetViews><sheetView workbookViewId=\"0\"><pane ySplit=\"4\" topLeftCell=\"A5\" activePane=\"bottomLeft\" state=\"frozen\"/></sheetView></sheetViews>"
        "<cols>"
        + "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate([16, 14, 20, 24, 14, 26, 18, 12, 12, 18, 12, 18, 14, 14, 18, 14, 14, 14, 20, 14, 14, 14, 16, 16, 20, 24, 14], start=1))
        + "</cols>"
        "<sheetData>"
        + "".join(xml_rows)
        + "</sheetData>"
        '<autoFilter ref="A4:AA14"/>'
        "</worksheet>"
    )


def write_file() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheets><sheet name=\"Purchase Upload\" sheetId=\"1\" r:id=\"rId1\"/></sheets>"
            "</workbook>",
        )
        zf.writestr(
            "xl/styles.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts>'
            '<fonts count="3"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="14"/><name val="Calibri"/></font></fonts>'
            '<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFEAF2F8"/></patternFill></fill></fills>'
            '<borders count="2"><border/><border><left style="thin"><color rgb="FFD9E2EC"/></left><right style="thin"><color rgb="FFD9E2EC"/></right><top style="thin"><color rgb="FFD9E2EC"/></top><bottom style="thin"><color rgb="FFD9E2EC"/></bottom></border></borders>'
            '<cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="1" applyFill="1" applyFont="1" applyBorder="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="1" applyNumberFormat="1" applyBorder="1"/><xf numFmtId="4" fontId="0" fillId="0" borderId="1" applyNumberFormat="1" applyBorder="1"/><xf numFmtId="0" fontId="2" fillId="0" borderId="0" applyFont="1"/><xf numFmtId="0" fontId="0" fillId="3" borderId="0" applyFill="1"/></cellXfs>'
            "</styleSheet>",
        )
        zf.writestr("xl/worksheets/sheet1.xml", make_sheet_xml())
        zf.writestr("docProps/core.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"/>')
        zf.writestr("docProps/app.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>TaxFlow</Application></Properties>')


if __name__ == "__main__":
    write_file()
    print(OUTPUT.resolve())
