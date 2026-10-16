#!/usr/bin/env python3
"""Writes the two platform documents in docs.json as Word files (.docx), standard library only.

The exceptions are real Word footnotes, the way an engineer writes them in Word: the footnote text lives in
word/footnotes.xml, and the sentence it qualifies carries only a footnote reference (a superscript number). The
footnote text doesn't repeat what it qualifies ("200 for keys on the EU-West cluster …") — on the page it doesn't
need to: the number points at the sentence. The demo itself only reads the .docx files; run this again only if you
change docs.json."""
import json
import zipfile
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
RPR = '<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr>'
MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def run(text: str) -> str:
    return f'<w:r>{RPR}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def build(doc: dict, author: str) -> bytes:
    paras = ['<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>' + run(doc["title"]) + "</w:p>",
             "<w:p>" + run(doc["header"]) + "</w:p>"]
    notes = []
    for n, (title, rule, foot, *rest) in enumerate(doc["sections"], 1):
        paras.append('<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>' + run(f"{n}. {title}") + "</w:p>")
        body = run(rule)
        if foot:
            notes.append(foot)
            body += (f'<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/><w:vertAlign w:val="superscript"/></w:rPr>'
                     f'<w:footnoteReference w:id="{len(notes)}"/></w:r>')
        if rest:
            body += run(" " + rest[0])
        paras.append("<w:p>" + body + "</w:p>")
    document = (f'{HEAD}<w:document {NS}><w:body>{"".join(paras)}'
                '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr></w:body></w:document>')
    footnotes = (f'{HEAD}<w:footnotes {NS}>'
                 '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
                 '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>'
                 + "".join(f'<w:footnote w:id="{k}"><w:p><w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr>'
                           f'<w:footnoteRef/></w:r>{run(" " + t)}</w:p></w:footnote>' for k, t in enumerate(notes, 1))
                 + "</w:footnotes>")
    core = (f'{HEAD}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(doc["title"])}</dc:title><dc:creator>{escape(author)}</dc:creator>'
            f'<cp:lastModifiedBy>{escape(author)}</cp:lastModifiedBy>'
            '<dcterms:created xsi:type="dcterms:W3CDTF">2026-01-05T09:00:00Z</dcterms:created>'
            '<dcterms:modified xsi:type="dcterms:W3CDTF">2026-10-01T09:00:00Z</dcterms:modified></cp:coreProperties>')
    types = (f'{HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             f'<Override PartName="/word/document.xml" ContentType="{MAIN}.document.main+xml"/>'
             f'<Override PartName="/word/footnotes.xml" ContentType="{MAIN}.footnotes+xml"/>'
             '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
             "</Types>")
    rels = (f'{HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
            'Target="docProps/core.xml"/></Relationships>')
    doc_rels = (f'{HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f'<Relationship Id="rId1" Type="{REL}/footnotes" Target="footnotes.xml"/></Relationships>')
    parts = [("[Content_Types].xml", types), ("_rels/.rels", rels), ("docProps/core.xml", core),
             ("word/_rels/document.xml.rels", doc_rels), ("word/document.xml", document),
             ("word/footnotes.xml", footnotes)]
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts:
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 1, 9, 0, 0))  # fixed: same bytes on every machine
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text)
    return buf.getvalue()


if __name__ == "__main__":
    data = json.loads((HERE / "docs.json").read_text(encoding="utf-8"))
    for doc in data["docs"]:
        path = HERE / (doc["file"] + ".docx")
        path.write_bytes(build(doc, data["author"]))
        print(f"wrote {path.name} ({sum(1 for s in doc['sections'] if s[2])} footnote(s))")
