#!/usr/bin/env python3
"""Writes the firm's precedent memos (data/memos/*.pdf) from data/firm.json.

Plain-text PDFs with the standard Type 1 fonts, no dependencies. Memos without explicit pages get the
house layout: page 1 = holding + facts, page 2 = firm position + practice notes.
Run it again after editing firm.json; demo.py uploads whatever is in data/memos/.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGE_W, PAGE_H = 612, 792  # US Letter
MARGIN = 72
LINE_CHARS = 98            # wrap width for 10.5 pt Helvetica across 468 pt

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
            cols = [MARGIN, MARGIN + 196, MARGIN + 340]
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


def pages_of(m: dict) -> list:
    if "pages" in m:
        return [[tuple(b) for b in page] for page in m["pages"]]
    meta = f"{m['court']}, decided {m['decided']} - memo by the employment group"
    return [[("title", f"Precedent memo: {m['case']}"), ("meta", meta),
             ("h", "1. Holding"), ("p", m["holding"]), ("h", "2. Facts"), ("p", m["facts"])],
            [("h", "3. Firm position"), ("p", m["position"]),
             ("h", "4. Practice notes"), ("p", "Keep the dated documents from the first client call. "
                                              "The dates usually decide these cases.")]]


def main() -> None:
    firm = json.loads((HERE / "firm.json").read_text(encoding="utf-8"))
    out = HERE / "memos"
    out.mkdir(exist_ok=True)
    for m in firm["memos"]:
        write_pdf(out / m["file"], f"Precedent memo: {m['case']}", pages_of(m))
    print(f"wrote {len(firm['memos'])} memos to {out.relative_to(HERE.parent)}/")


if __name__ == "__main__":
    main()
