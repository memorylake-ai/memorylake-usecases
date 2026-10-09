#!/usr/bin/env python3
"""Regenerate the PDF source files in data/sources/ (standard library only).

They stand in for a credit union's published member documents: two versions of the
fee schedule, the funds availability policy (with a hold table) and the wire
transfer procedures. The committed files are what the demo uploads; run this only
if you edit the text below.

The PDFs are written uncompressed with one text-show operator per line, so that
demo.py can read each page's text layer back out of the *downloaded original* and
check a citation against it without any PDF library. Text is ASCII on purpose.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources"

PAGE_W, PAGE_H = 612, 792  # US Letter
MARGIN = 72
LINE_CHARS = 98            # wrap width for 10.5 pt Helvetica across 468 pt

# Each document: provenance (written to the Library item's x_attrs) + pages.
# A page is a list of blocks: ("title"|"meta"|"h"|"p", text) or ("table", caption, rows).
DOCS = {
    "larch-fee-schedule-2025.pdf": {
        "provenance": {"title": "Schedule of Fees and Charges", "doc_type": "fee-schedule", "version": "2025.1", "effective": "2025-01-01",
                       "superseded": "2026-01-01", "publisher": "Larch Valley Credit Union, Member Services"},
        "pages": [
            [("title", "Schedule of Fees and Charges"),
             ("meta", "Larch Valley Credit Union. Version 2025.1, effective January 1, 2025."),
             ("h", "1. Checking accounts"),
             ("p", "Everyday Checking has no monthly maintenance fee. Premier Checking has a monthly fee of 12 dollars, "
                   "waived when the combined balance of the member's accounts is at least 5,000 dollars on the last "
                   "day of the statement period."),
             ("h", "2. Overdrafts"),
             ("p", "Overdraft fee: 35 dollars per item paid into overdraft. We charge no more than 4 overdraft fees "
                   "per business day. No fee is charged when the account is overdrawn by 10 dollars or less."),
             ("p", "Overdraft coverage for one-time debit card and ATM transactions applies only to members who opt in. "
                   "Members can opt out at any time by calling Member Services or in online banking.")],
            [("h", "3. Wires and other services"),
             ("p", "Outgoing domestic wire: 25 dollars. Outgoing international wire: 45 dollars. Incoming wires: no fee."),
             ("p", "Stop payment order: 30 dollars per item. Cashier's check: 8 dollars.")],
        ],
    },
    "larch-fee-schedule-2026.pdf": {
        "provenance": {"title": "Schedule of Fees and Charges", "doc_type": "fee-schedule", "version": "2026.1", "effective": "2026-01-01",
                       "supersedes": "2025.1", "publisher": "Larch Valley Credit Union, Member Services"},
        "pages": [
            [("title", "Schedule of Fees and Charges"),
             ("meta", "Larch Valley Credit Union. Version 2026.1, effective January 1, 2026. Replaces version 2025.1."),
             ("h", "1. Checking accounts"),
             ("p", "Everyday Checking has no monthly maintenance fee. Premier Checking has a monthly fee of 12 dollars, "
                   "waived when the combined balance of the member's accounts is at least 2,500 dollars on the last "
                   "day of the statement period."),
             ("h", "2. Overdrafts"),
             ("p", "Overdraft fee: 29 dollars per item paid into overdraft. We charge no more than 3 overdraft fees "
                   "per business day. No fee is charged when the account is overdrawn by 50 dollars or less."),
             ("p", "Overdraft coverage for one-time debit card and ATM transactions applies only to members who opt in. "
                   "Members can opt out at any time by calling Member Services or in online banking.")],
            [("h", "3. Wires and other services"),
             ("p", "Outgoing domestic wire: 25 dollars. Outgoing international wire: 40 dollars. Incoming wires: no fee."),
             ("p", "Stop payment order: 25 dollars per item. Cashier's check: 8 dollars.")],
        ],
    },
    "larch-funds-availability-policy.pdf": {
        "provenance": {"title": "Funds Availability Policy", "doc_type": "policy", "version": "2024.3", "effective": "2024-07-01",
                       "publisher": "Larch Valley Credit Union, Deposit Operations"},
        "pages": [
            [("title", "Funds Availability Policy"),
             ("meta", "Larch Valley Credit Union. Version 2024.3, effective July 1, 2024."),
             ("h", "1. Our general policy"),
             ("p", "Our policy is to make funds from cash and electronic direct deposits available on the day we "
                   "receive the deposit. Funds from check deposits are generally available on the first business "
                   "day after the day of deposit."),
             ("h", "2. Business days"),
             ("p", "A business day is Monday through Friday, excluding federal holidays. Deposits received after "
                   "5:00 pm Pacific time on a business day are treated as received on the next business day.")],
            [("h", "3. Longer delays may apply"),
             ("p", "In some cases we will not make all of the funds from a check deposit available on the first "
                   "business day. The table below shows when funds become available in those cases."),
             ("table", "Table 1. Extended holds on check deposits",
              [["Situation", "First available", "Remainder available"],
               ["Deposits over 5,525 dollars on one day", "First 225 dollars next day", "7th business day"],
               ["New account, first 30 days", "First 225 dollars next day", "9th business day"],
               ["Redeposited returned check", "None", "7th business day"],
               ["Repeated overdrafts in last 6 months", "First 225 dollars next day", "7th business day"]]),
             ("p", "If we delay availability, we will tell you when the funds will be available, at the time of the "
                   "deposit or by mail no later than the first business day after the deposit.")],
        ],
    },
    "larch-wire-transfer-procedures.pdf": {
        "provenance": {"title": "Wire Transfer Procedures for Members", "doc_type": "procedure", "version": "2026.2", "effective": "2026-06-15",
                       "publisher": "Larch Valley Credit Union, Payments Operations"},
        "pages": [
            [("title", "Wire Transfer Procedures for Members"),
             ("meta", "Larch Valley Credit Union. Version 2026.2, effective June 15, 2026."),
             ("h", "1. Cutoff times"),
             ("p", "Outgoing domestic wire requests received by 3:00 pm Eastern time on a business day are sent the "
                   "same day. Requests received after 3:00 pm Eastern time are sent on the next business day."),
             ("p", "Outgoing international wires must be requested by 1:00 pm Eastern time and are usually credited "
                   "to the beneficiary bank within 2 business days."),
             ("h", "2. Verification"),
             ("p", "Every new wire recipient is verified by a call-back to the phone number on file before the first "
                   "wire is released. Wires of 10,000 dollars or more always require a call-back."),
             ("h", "3. Recalls"),
             ("p", "A wire can be recalled only before it is released. After release we can ask the receiving bank "
                   "to return the funds, but the return is not guaranteed.")],
        ],
    },
}


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
    info = add(f"<< /Title ({esc(title)}) /Producer (memorylake-usecases make_sources.py) >>".encode())

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


def main() -> None:
    SOURCES.mkdir(exist_ok=True)
    provenance = {}
    for name, spec in DOCS.items():
        title = next(b[1] for b in spec["pages"][0] if b[0] == "title")
        write_pdf(SOURCES / name, title, spec["pages"])
        provenance[name] = spec["provenance"]
        print(f"wrote {name} ({len(spec['pages'])} page(s))")
    (HERE / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print("wrote provenance.json")


if __name__ == "__main__":
    main()
