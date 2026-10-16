#!/usr/bin/env python3
"""Writes the three job descriptions in jds.json as Word documents (.docx), standard library only.

Two of them were edited with Track Changes on and saved without accepting the changes, which is how most edited
Word files are saved: every edit is a deletion (w:del, the old words kept in w:delText) followed by an insertion
(w:ins). Word shows the new words; the old ones are still in the file. The demo itself only reads the .docx files;
run this again only if you change jds.json."""
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
RPR = '<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr>'


def build(jd: dict) -> bytes:
    ids = iter(range(1, 1000))
    who = escape(jd["editor"].split(" (")[0]) if jd["editor"] else ""
    paras = []
    for k, parts in enumerate(jd["paragraphs"]):
        runs = []
        for part in parts:
            if isinstance(part, str):
                runs.append(f'<w:r>{RPR}<w:t xml:space="preserve">{escape(part)}</w:t></w:r>')
                continue
            old, new = part
            stamp = f'w:author="{who}" w:date="{jd["edited"]}"'
            runs.append(f'<w:del w:id="{next(ids)}" {stamp}><w:r w:rsidDel="00B71C2A">{RPR}'
                        f'<w:delText xml:space="preserve">{escape(old)}</w:delText></w:r></w:del>')
            runs.append(f'<w:ins w:id="{next(ids)}" {stamp}><w:r w:rsidR="00B71C2A">{RPR}'
                        f'<w:t xml:space="preserve">{escape(new)}</w:t></w:r></w:ins>')
        style = '<w:pPr><w:pStyle w:val="Title"/></w:pPr>' if k == 0 else ""
        paras.append(f'<w:p w:rsidR="004A2F11" w:rsidRDefault="004A2F11">{style}{"".join(runs)}</w:p>')
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NS}><w:body>{"".join(paras)}'
           '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr></w:body></w:document>')
    settings = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:settings {NS}>'
                + ('<w:trackRevisions/>' if who else '') + '</w:settings>')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties '
            'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(jd["title"])}</dc:title><dc:creator>Kestrel Freight Talent Team</dc:creator>'
            f'<cp:lastModifiedBy>{who or "Kestrel Freight Talent Team"}</cp:lastModifiedBy>'
            '<dcterms:created xsi:type="dcterms:W3CDTF">2026-09-01T09:00:00Z</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{jd["edited"] or "2026-09-01T09:00:00Z"}</dcterms:modified>'
            '</cp:coreProperties>')
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
             '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
             '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
             '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '</Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
                '</Relationships>')
    from io import BytesIO
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in (("[Content_Types].xml", types), ("_rels/.rels", rels), ("docProps/core.xml", core),
                           ("word/_rels/document.xml.rels", doc_rels), ("word/document.xml", doc),
                           ("word/settings.xml", settings)):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 1, 9, 0, 0))  # fixed: same bytes on every machine
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text)
    return buf.getvalue()


if __name__ == "__main__":
    data = json.loads((HERE / "jds.json").read_text(encoding="utf-8"))
    for jd in data["jds"]:
        out = HERE / (jd["file"] + ".docx")
        out.write_bytes(build(jd))
        print(f"wrote {out.name} ({out.stat().st_size} bytes)")
