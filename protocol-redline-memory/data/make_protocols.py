#!/usr/bin/env python3
"""Writes the three study documents in protocols.json as Word files (.docx), standard library only.

The two protocol amendments are marked up the way many sponsors send them: by hand, not with Word's Track Changes.
Deleted text is an ordinary run with strikethrough (<w:strike/>), new text an ordinary run in bold and underline.
There is no <w:del> or <w:ins> anywhere in the file: Review › Accept All Changes has nothing to accept. The demo
itself only reads the .docx files; run this again only if you change protocols.json."""
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
    """kind: "" plain · "del" struck through · "ins" bold + underline · "label" bold."""
    props = {"": "", "del": "<w:strike/>", "ins": '<w:b/><w:u w:val="single"/>', "label": "<w:b/>"}[kind]
    return f'<w:r><w:rPr>{FONT}{props}{SIZE}</w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def paragraph(label: str, parts: list) -> str:
    runs = [run(f"{label}: ", "label")] if label != "Legend" else []
    for p in parts:
        runs.append(run(p) if isinstance(p, str) else run(p[1], p[0]))
    return "<w:p>" + "".join(runs) + "</w:p>"


def build(doc: dict, sponsor: str) -> bytes:
    paras = ['<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>' + run(doc["title"], "label") + "</w:p>",
             "<w:p>" + run(f"{sponsor} · {doc['study']} · {doc['saved']}") + "</w:p>"]
    paras += [paragraph(label, parts) for label, parts in doc["lines"]]
    body = (f'{HEAD}<w:document {NS}><w:body>{"".join(paras)}'
            '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr></w:body></w:document>')
    core = (f'{HEAD}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(doc["title"])}</dc:title><dc:creator>{escape(sponsor)}</dc:creator>'
            f'<cp:lastModifiedBy>{escape(sponsor)}</cp:lastModifiedBy>'
            '<dcterms:created xsi:type="dcterms:W3CDTF">2026-06-01T09:00:00Z</dcterms:created>'
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
    data = json.loads((HERE / "protocols.json").read_text(encoding="utf-8"))
    for doc in data["docs"]:
        path = HERE / (doc["file"] + ".docx")
        path.write_bytes(build(doc, data["sponsor"]))
        n = sum(1 for _, parts in doc["lines"] for p in parts if not isinstance(p, str) and p[0] == "del")
        print(f"wrote {path.name} ({n} struck-through edit(s))")
