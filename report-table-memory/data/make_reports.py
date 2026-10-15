#!/usr/bin/env python3
"""Writes the desk's sources from data/desk.json: one quarterly IR report per company as a PDF (data/reports/)
and the same KPI table as an .xlsx model export (data/models/).

Standard library only. The PDFs are plain-text PDFs with the standard Type 1 fonts; each table cell is drawn
where the column starts, so the text layer keeps the cell boundaries. The .xlsx cells hold typed numbers.
The PDF prints numbers the way the company prints them (412,6 in Germany, Italy and the Netherlands;
154.2 in the UK and US); the .xlsx holds the same numbers as numbers.

    python3 data/make_reports.py
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
PAGE_W, PAGE_H = 612, 792  # US Letter
MARGIN = 72
LINE_CHARS = 98            # wrap width for 10.5 pt Helvetica across 468 pt
FIXED = (2026, 10, 15, 9, 0, 0)  # zip entry timestamps, so the .xlsx bytes are reproducible

def wrap(text: str, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    if line:
        lines.append(line)
    return lines


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def page_stream(blocks: list) -> bytes:
    out, y = [], PAGE_H - MARGIN

    def text(s: str, font: str, size: float, x: float = MARGIN) -> None:
        out.append(f"BT /{font} {size} Tf {x:.1f} {y:.1f} Td ({esc(s)}) Tj ET")

    for block in blocks:
        kind = block[0]
        if kind == "title":
            text(block[1], "F2", 18); y -= 26
        elif kind == "meta":
            text(block[1], "F3", 10); y -= 24
        elif kind == "h":
            text(block[1], "F2", 12.5); y -= 18
        elif kind == "p":
            for ln in wrap(block[1], LINE_CHARS):
                text(ln, "F1", 10.5); y -= 14
            y -= 10
        elif kind == "table":
            _, caption, rows = block
            text(caption, "F2", 10.5); y -= 18
            cols = [MARGIN, MARGIN + 220, MARGIN + 340][:len(rows[0])]
            right = PAGE_W - MARGIN
            top = y + 12
            for i, row in enumerate(rows):
                for x, cell in zip(cols, row):
                    text(cell, "F2" if i == 0 else "F1", 9.5, x + 4)
                y -= 18
                out.append(f"{MARGIN} {y + 12:.1f} m {right} {y + 12:.1f} l S")
            out.append(f"{MARGIN} {top:.1f} m {right} {top:.1f} l S")
            for x in cols + [right]:
                out.append(f"{x} {top:.1f} m {x} {y + 12:.1f} l S")
            y -= 14
    return "\n".join(out).encode("latin-1")


def write_pdf(path: Path, title: str, pages: list) -> None:
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    fonts = [add(f"<< /Type /Font /Subtype /Type1 /BaseFont /{n} /Encoding /WinAnsiEncoding >>".encode())
             for n in ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique")]
    pages_id = len(objs) + 1 + 2 * len(pages)  # reserved: allocated after the page objects
    kids = []
    for blocks in pages:
        data = page_stream(blocks)
        content = add(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
        kids.append(add((f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                         f"/Resources << /Font << /F1 {fonts[0]} 0 R /F2 {fonts[1]} 0 R /F3 {fonts[2]} 0 R >> >> "
                         f"/Contents {content} 0 R >>").encode()))
    assert add(f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()) == pages_id
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
    info = add(f"<< /Title ({esc(title)}) /Producer (memorylake-usecases make_memos.py) >>".encode())

    buf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(buf))
        buf += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(buf)
    buf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        buf += b"%010d 00000 n \n" % off
    buf += (f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    path.write_bytes(bytes(buf))


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


def write_xlsx(path: Path, sheet: str, rows: list[list]) -> None:
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

def number(s: str, convention: str) -> float:
    """A printed cell as a number: "1.204" / "412,6" (de) or "1,204" / "154.2" (en)."""
    if convention == "de":
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    v = float(s)
    return int(v) if v.is_integer() else v


def main() -> None:
    desk = json.loads((HERE / "desk.json").read_text(encoding="utf-8"))
    (HERE / "reports").mkdir(exist_ok=True)
    (HERE / "models").mkdir(exist_ok=True)
    for c in desk["companies"]:
        rows = [c["columns"]] + [[r["label"], *r["cells"]] for r in c["rows"]]
        write_pdf(HERE / "reports" / c["pdf"], c["title"],
                  [[("title", c["title"]), ("meta", c["meta"]), ("h", c["heading"]),
                    ("table", c["caption"], rows), ("p", c["note"])]])
        typed = [c["columns"]] + [[r["label"], *(number(v, c["convention"]) for v in r["cells"])] for r in c["rows"]]
        write_xlsx(HERE / "models" / c["xlsx"], "KPIs", typed)
    print(f"wrote {len(desk['companies'])} PDF reports to data/reports/ and {len(desk['companies'])} .xlsx models to data/models/")


if __name__ == "__main__":
    main()
