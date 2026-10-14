#!/usr/bin/env python3
"""
Writes the campaign exports in data/sources/ (already committed; run this only to change them).

Four tools, four exports, one row per campaign. Each tool reports conversion on its own yardstick,
and the yardstick is written in the column headers: what counts as an order, and what it is divided by.
Standard library only: an .xlsx is a zip of a few XML parts, written here with inline strings.
One worksheet per export, because a search hit names one sheet per document.

    python3 data/make_sources.py
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent / "sources"
FIXED = (2026, 10, 1, 9, 0, 0)  # zip entry timestamps, so the bytes (and their sha256) are reproducible

EXPORTS = {
    "email-esp-2026.xlsx": ("Campaigns", [
        ["Campaign", "Sent on", "Delivered", "Unique clicks", "Orders credited by ESP (5-day click)", "Orders / unique clicks (%)"],
        ["Spring Trail email", "2026-03-10", 61200, 4204, 412, 9.8],
        ["Summer Gear-Up email", "2026-06-02", 60450, 4000, 356, 8.9],
    ]),
    "paid-social-2026.xlsx": ("Campaigns", [
        ["Campaign", "Ran", "Impressions", "Link clicks", "Purchases (7-day click + 1-day view)", "Purchases / link clicks (%)"],
        ["Trailhead paid social", "2026-06-03 to 2026-06-30", 1840000, 13000, 533, 4.1],
        ["Fall Layers paid social", "2026-09-01 to 2026-09-28", 1712000, 13000, 468, 3.6],
    ]),
    "search-ads-2026.xlsx": ("Campaigns", [
        ["Campaign", "Ran", "Clicks", "Conversions (data-driven, fractional)", "Conversions / clicks (%)"],
        ["Spring brand search", "2026-03-01 to 2026-03-31", 7015, 182.4, 2.6],
        ["Fall non-brand search", "2026-09-01 to 2026-09-30", 7500, 165, 2.2],
    ]),
    "site-analytics-2026.xlsx": ("Campaigns", [
        ["Campaign", "Channel", "Landing sessions", "Orders (last non-direct click)", "Orders / landing sessions (%)"],
        ["Spring Trail email", "email", 4380, 131, 3.0],
        ["Spring brand search", "search", 6820, 341, 5.0],
        ["Summer Gear-Up email", "email", 4150, 108, 2.6],
        ["Trailhead paid social", "paid social", 11200, 146, 1.3],
        ["Ridge Notes creator collab", "creator", 2950, 38, 1.3],
        ["Fall Layers paid social", "paid social", 10900, 120, 1.1],
        ["Fall non-brand search", "search", 7240, 304, 4.2],
    ]),
}


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
    for name, (sheet, rows) in EXPORTS.items():
        write(OUT / name, sheet, rows)
    for p in sorted(OUT.iterdir()):
        print(f"{p.name:<32} {p.stat().st_size:>6} bytes")


if __name__ == "__main__":
    main()
