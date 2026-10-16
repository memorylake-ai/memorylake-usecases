#!/usr/bin/env python3
"""Writes the two launch fact sheets in factsheets.json as Word files (.docx), standard library only.

Product marketing marks the status of each claim with the highlighter, the way many teams do: yellow for a claim
still waiting for test-lab or legal sign-off, green for wording legal has cleared. The highlight is a run property
(<w:highlight w:val="yellow"/>) on otherwise ordinary text; the legend at the top of each sheet says what the
colours mean. The demo itself only reads the .docx files; run this again only if you change factsheets.json."""
import json
import zipfile
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
FONT = '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
SIZE = '<w:sz w:val="22"/>'


def run(text: str, kind: str = "") -> str:
    """kind: "" plain · "yellow" / "green" highlighted · "label" bold."""
    props = {"": "", "yellow": '<w:highlight w:val="yellow"/>', "green": '<w:highlight w:val="green"/>',
             "label": "<w:b/>"}[kind]
    return f'<w:r><w:rPr>{FONT}{props}{SIZE}</w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def paragraph(label: str, parts: list) -> str:
    runs = [run(f"{label}: ", "label")]
    for p in parts:
        runs.append(run(p) if isinstance(p, str) else run(p[1], p[0]))
    return "<w:p>" + "".join(runs) + "</w:p>"


def build(doc: dict, company: str, legend: str) -> bytes:
    paras = ['<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>' + run(doc["title"], "label") + "</w:p>",
             "<w:p>" + run(f"{company} · {doc['product']} · saved {doc['saved']}") + "</w:p>",
             "<w:p>" + run(legend) + "</w:p>"]
    paras += [paragraph(label, parts) for label, parts in doc["lines"]]
    body = (f'{HEAD}<w:document {NS}><w:body>{"".join(paras)}'
            '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr></w:body></w:document>')
    core = (f'{HEAD}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(doc["title"])}</dc:title><dc:creator>{escape(company)}</dc:creator>'
            f'<cp:lastModifiedBy>{escape(company)}</cp:lastModifiedBy>'
            '<dcterms:created xsi:type="dcterms:W3CDTF">2026-09-01T09:00:00Z</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{doc["saved"]}T09:00:00Z</dcterms:modified></cp:coreProperties>')
    types = (f'{HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             f'<Override PartName="/word/document.xml" ContentType="{MAIN}.document.main+xml"/>'
             '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
             "</Types>")
    rels = (f'{HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
            'Target="docProps/core.xml"/></Relationships>')
    doc_rels = f'{HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>'
    parts = [("[Content_Types].xml", types), ("_rels/.rels", rels), ("docProps/core.xml", core),
             ("word/_rels/document.xml.rels", doc_rels), ("word/document.xml", body)]
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts:
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 1, 9, 0, 0))  # fixed: same bytes on every machine
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text)
    return buf.getvalue()


if __name__ == "__main__":
    data = json.loads((HERE / "factsheets.json").read_text(encoding="utf-8"))
    for doc in data["docs"]:
        path = HERE / (doc["file"] + ".docx")
        path.write_bytes(build(doc, data["company"], data["legend"]))
        hl = [p[0] for _, parts in doc["lines"] for p in parts if not isinstance(p, str)]
        print(f"wrote {path.name} ({hl.count('yellow')} yellow, {hl.count('green')} green highlight(s))")
