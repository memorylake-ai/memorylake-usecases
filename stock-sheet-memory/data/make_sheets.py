#!/usr/bin/env python3
"""Writes the weekly stock count from data/stock.json as two .xlsx workbooks with the same numbers:

  stock-count-2026-w42.xlsx        one sheet per warehouse, named after it — how the team keeps the count
  stock-count-2026-w42-sited.xlsx  the same sheets, with a Site column on every row

Standard library only. Cells hold typed numbers; zip entries carry a fixed timestamp, so the bytes are reproducible.

    python3 data/make_sheets.py
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
FIXED = (2026, 10, 12, 9, 0, 0)  # zip entry timestamps, so the .xlsx bytes are reproducible


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


def write_xlsx(path: Path, sheets: list[tuple[str, list[list]]]) -> None:
    def part(z: zipfile.ZipFile, name: str, xml: str) -> None:
        z.writestr(zipfile.ZipInfo(name, FIXED), '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + xml,
                   compress_type=zipfile.ZIP_DEFLATED)

    n = len(sheets)
    with zipfile.ZipFile(path, "w") as z:
        part(z, "[Content_Types].xml",
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
             + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                       for i in range(1, n + 1))
             + '</Types>')
        part(z, "_rels/.rels",
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
             '</Relationships>')
        part(z, "xl/workbook.xml",
             '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
             'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
             + "".join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1))
             + '</sheets></workbook>')
        part(z, "xl/_rels/workbook.xml.rels",
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                       for i in range(1, n + 1))
             + '</Relationships>')
        for i, (_, rows) in enumerate(sheets, 1):
            out = []
            for r, row in enumerate(rows, start=1):
                cells = "".join(cell(f"{col(c)}{r}", v) for c, v in enumerate(row or []) if v not in (None, ""))
                if cells:
                    out.append(f'<row r="{r}">{cells}</row>')
            part(z, f"xl/worksheets/sheet{i}.xml",
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                 f'<sheetData>{"".join(out)}</sheetData></worksheet>')


def sheets(d: dict, sited: bool) -> list[tuple[str, list[list]]]:
    out = []
    for s in d["sites"]:
        head = (["Site"] if sited else []) + d["columns"]
        rows = [([s["site"]] if sited else []) + [i["sku"], i["item"], s["on_hand"][i["sku"]], i["reorder"]]
                for i in d["items"]]
        out.append((s["site"], [head] + rows))
    return out


def main() -> None:
    d = json.loads((HERE / "stock.json").read_text(encoding="utf-8"))
    write_xlsx(HERE / d["file"], sheets(d, sited=False))
    write_xlsx(HERE / d["file_sited"], sheets(d, sited=True))
    print(f"wrote data/{d['file']} and data/{d['file_sited']} ({len(d['sites'])} sheets × {len(d['items'])} rows each)")


if __name__ == "__main__":
    main()
