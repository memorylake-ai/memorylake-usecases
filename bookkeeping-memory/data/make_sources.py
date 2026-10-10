#!/usr/bin/env python3
"""
Writes the ledger exports in data/sources/ (already committed; run this only to change them).

Standard library only: an .xlsx is a zip of a few XML parts, written here with inline strings.
Every export holds one worksheet, because a search hit names one sheet per document,
not per passage.

    python3 data/make_sources.py
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent / "sources"
FIXED = (2026, 9, 30, 9, 0, 0)  # zip entry timestamps, so the bytes (and their sha256) are reproducible

HEAD = ["Date", "Payee", "Memo", "Amount", "Category"]

FERNHILL_Q3 = [
    ["2026-06-02", "Paylane", "Card processing fees May", -204.18, "Cost of revenue"],
    ["2026-06-03", "Northwind Hosting", "Servers Jun", -1480, "Cost of revenue"],
    ["2026-06-05", "Brightdesk Coworking", "Desk rental Jun", -650, "Rent"],
    ["2026-06-10", "Paylane", "Payout", 16210.44, "Clearing"],
    ["2026-06-14", "Teal Freight", "Pallet shipping", -371.2, "Shipping and delivery"],
    ["2026-06-22", "Copperleaf Office", "Printer paper and toner", -88.5, "Office supplies"],
    ["2026-07-02", "Paylane", "Card processing fees Jun", -219.06, "Cost of revenue"],
    ["2026-07-03", "Northwind Hosting", "Servers Jul", -1480, "Cost of revenue"],
    ["2026-07-05", "Brightdesk Coworking", "Desk rental Jul", -650, "Rent"],
    ["2026-07-10", "Paylane", "Payout", 17388.9, "Clearing"],
    ["2026-07-16", "Teal Freight", "Pallet shipping", -398.75, "Shipping and delivery"],
    ["2026-07-24", "Juniper Legal LLP", "Trademark filing", -1250, "Legal and professional"],
    ["2026-08-04", "Paylane", "Card processing fees Jul", -226.4, "Cost of revenue"],
    ["2026-08-04", "Northwind Hosting", "Servers Aug", -1480, "Cost of revenue"],
    ["2026-08-05", "Brightdesk Coworking", "Desk rental Aug", -650, "Rent"],
    ["2026-08-11", "Paylane", "Payout", 18020.15, "Clearing"],
    ["2026-08-19", "Teal Freight", "Pallet shipping", -412.3, "Shipping and delivery"],
    ["2026-08-27", "Copperleaf Office", "Shipping labels", -64.9, "Office supplies"],
]
FERNHILL_RECURRING = [
    ["Vendor", "Cadence", "Typical amount", "Treatment"],
    ["Paylane", "monthly", "200 to 240", "Fees: Cost of revenue. Payouts: Clearing"],
    ["Northwind Hosting", "monthly", "1480", "Cost of revenue"],
    ["Brightdesk Coworking", "monthly", "650", "Rent"],
]

MARROW_Q3 = [
    ["2026-06-03", "Paylane", "Card processing fees May", -71.32, "Bank and merchant fees"],
    ["2026-06-04", "Hollis Dairy", "Milk and cream", -612.4, "Cost of goods sold"],
    ["2026-06-09", "Forkful", "Delivery payout, net", 1874.1, "Delivery sales"],
    ["2026-06-18", "Saltmarsh Utilities", "Electricity Jun", -286.75, "Utilities"],
    ["2026-07-02", "Paylane", "Card processing fees Jun", -79.6, "Bank and merchant fees"],
    ["2026-07-03", "Hollis Dairy", "Milk and cream", -640.15, "Cost of goods sold"],
    ["2026-07-08", "Forkful", "Delivery payout, net", 2011.55, "Delivery sales"],
    ["2026-07-17", "Saltmarsh Utilities", "Electricity Jul", -301.2, "Utilities"],
    # August was booked by a new junior: the card fees went to the wrong account.
    ["2026-08-04", "Paylane", "Card processing fees Jul", -83.95, "Cost of revenue"],
    ["2026-08-05", "Hollis Dairy", "Milk and cream", -655.8, "Cost of goods sold"],
    ["2026-08-11", "Forkful", "Delivery payout, net", 2096.3, "Delivery sales"],
    ["2026-08-19", "Saltmarsh Utilities", "Electricity Aug", -318.4, "Utilities"],
]
MARROW_SEP = [
    ["2026-09-03", "Hollis Dairy", "Milk and cream", -662.1, "Cost of goods sold"],
    ["2026-09-08", "Forkful", "Delivery payout, net", 2140.6, "Delivery sales"],
    ["2026-09-12", "Orchard Print Co", "Menus reprint", -430, "Marketing"],
]

# What the first September export looked like: a download that broke half way. Not a workbook at all.
CORRUPT = b"PK\x03\x04" + b"\x00" * 26 + b"marrow-ledger-2026-09.xlsx: transfer interrupted\n" * 12


def col(i: int) -> str:
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def cell(ref: str, v) -> str:
    if isinstance(v, (int, float)):
        return f'<c r="{ref}"><v>{v}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>'


def write(path: Path, sheet: str, rows: list[list]) -> None:
    def part(z: zipfile.ZipFile, name: str, xml: str) -> None:
        z.writestr(zipfile.ZipInfo(name, FIXED), '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + xml,
                   compress_type=zipfile.ZIP_DEFLATED)

    out = []
    for r, row in enumerate(rows, start=1):
        cells = "".join(cell(f"{col(c)}{r}", v) for c, v in enumerate(row or []) if v not in (None, ""))
        if cells:
            out.append(f'<row r="{r}">{cells}</row>')
    with zipfile.ZipFile(path, "w") as z:
        part(z, "[Content_Types].xml",
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
             '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
             '</Types>')
        part(z, "_rels/.rels",
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
             '</Relationships>')
        part(z, "xl/workbook.xml",
             '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
             'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
             f'<sheets><sheet name="{escape(sheet)}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        part(z, "xl/_rels/workbook.xml.rels",
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
             '</Relationships>')
        part(z, "xl/worksheets/sheet1.xml",
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<sheetData>{"".join(out)}</sheetData></worksheet>')


def main() -> None:
    OUT.mkdir(exist_ok=True)
    # Fernhill's sheet holds two tables: the ledger, then (after two blank rows) a recurring-vendor list.
    write(OUT / "fernhill-ledger-2026-q3.xlsx", "Ledger",
          [HEAD, *FERNHILL_Q3, [], [], ["Recurring vendors"], *FERNHILL_RECURRING])
    write(OUT / "marrow-ledger-2026-q3.xlsx", "Ledger", [HEAD, *MARROW_Q3])
    write(OUT / "marrow-ledger-2026-09.xlsx", "Transactions", [HEAD, *MARROW_SEP])
    (OUT / "marrow-ledger-2026-09-broken.xlsx").write_bytes(CORRUPT)
    for p in sorted(OUT.iterdir()):
        print(f"{p.name:<40} {p.stat().st_size:>6} bytes")


if __name__ == "__main__":
    main()
