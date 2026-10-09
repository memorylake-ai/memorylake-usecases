#!/usr/bin/env python3
"""Regenerate the two .docx source files in data/sources/ (standard library only).

They stand in for notes exported from other tools: a go-live runbook exported from
Notion, and a call summary exported from Fathom. The committed files are what the
demo uploads; run this only if you edit the text below.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources"

DOCS = {
    "riverside-go-live-runbook.docx": [
        ("h", "Riverside cutover — go-live runbook"),
        ("i", "Exported from Notion · Halden Freight operations wiki · last edited 2026-10-02 by Ana Ruiz"),
        ("h2", "Scope"),
        ("p", "Move all picking, packing and dispatch at the Riverside warehouse from the legacy system to the new "
              "warehouse system. Cutover date: October 21, 2026 (moved from October 14 after the scanner vendor delay)."),
        ("h2", "Go-live risks"),
        ("p", "1. Label printer firmware. The twelve label printers must be upgraded to firmware v4.2 before go-live, "
              "or pick tickets print blank. Owner: Cara Lind with the printer vendor."),
        ("p", "2. Handheld scanner delivery. Handhelds arrive October 16; any further slip moves the cutover again."),
        ("p", "3. Carrier pickups. Carriers must be told the new date one week ahead (done by Ana Ruiz on 2026-09-29)."),
        ("h2", "Rollback"),
        ("p", "If more than 2 percent of orders fail to print or scan on day one, switch back to the legacy system "
              "before the 14:00 carrier pickup. Decision owner: Ana Ruiz."),
    ],
    "scanner-vendor-pricing-call.docx": [
        ("h", "Scanner vendor — pricing call summary (CONFIDENTIAL)"),
        ("i", "Exported from Fathom · call on 2026-09-25 · Ana Ruiz with the vendor's account manager"),
        ("p", "Negotiated unit price for the 40 handheld scanners: 412 USD each, down from a 448 USD list price."),
        ("p", "Volume discount of 8 percent if Halden orders a second batch for the Eastgate warehouse before March 2027."),
        ("p", "Late-delivery penalty: 1.5 percent of the order value per week of delay, capped at 6 percent."),
        ("p", "Confidential: the vendor asked that these terms are not shared outside the operations leadership team."),
    ],
}

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>')
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>')


def paragraph(kind: str, text: str) -> str:
    props = {"h": "<w:b/><w:sz w:val='36'/>", "h2": "<w:b/><w:sz w:val='28'/>", "i": "<w:i/><w:color w:val='666666'/>"}
    rpr = f"<w:rPr>{props[kind]}</w:rPr>" if kind in props else ""
    return f'<w:p><w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def write_docx(path: Path, blocks: list[tuple[str, str]]) -> None:
    body = "".join(paragraph(k, t) for k, t in blocks)
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:body>{body}</w:body></w:document>')
    # A fixed timestamp keeps the bytes identical across regenerations.
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in (("[Content_Types].xml", CONTENT_TYPES), ("_rels/.rels", RELS),
                           ("word/document.xml", document)):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 2, 9, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)


def plain_text(name: str) -> str:
    return "\n".join(t for _, t in DOCS[name])


if __name__ == "__main__":
    SOURCES.mkdir(exist_ok=True)
    for name, blocks in DOCS.items():
        write_docx(SOURCES / name, blocks)
        print(f"wrote {SOURCES / name}")
