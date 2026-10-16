#!/usr/bin/env python3
"""Writes the three engagement letters in letters.json as Word documents (.docx), standard library only.

Two of them carry the partner's review comments the way Word stores them: the comment text in word/comments.xml,
and in the letter body only a marker around the clause it is on (w:commentRangeStart / w:commentRangeEnd /
w:commentReference). The comments don't say which clause they are about — in Word they don't need to: the balloon
points at it. The demo itself only reads the .docx files; run this again only if you change letters.json."""
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


def build(letter: dict, partner: str) -> bytes:
    initials = "".join(w[0] for w in partner.replace(".", " ").split())
    paras = ['<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>' + run(letter["title"]) + "</w:p>",
             "<w:p>" + run(letter["header"]) + "</w:p>"]
    notes = []
    for n, (title, text) in enumerate(letter["clauses"], 1):
        body = run(f"{n}. {title}. {text}")
        comment = letter["comments"].get(str(n))
        if comment:
            k = len(notes)
            notes.append(comment)
            body = (f'<w:commentRangeStart w:id="{k}"/>{body}<w:commentRangeEnd w:id="{k}"/>'
                    f'<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr><w:commentReference w:id="{k}"/></w:r>')
        paras.append(f"<w:p>{body}</w:p>")
    paras.append("<w:p>" + run(letter["signoff"]) + "</w:p>")
    doc = (f'{HEAD}<w:document {NS}><w:body>{"".join(paras)}'
           '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr></w:body></w:document>')
    comments = (f'{HEAD}<w:comments {NS}>' + "".join(
        f'<w:comment w:id="{k}" w:author="{escape(partner)}" w:date="{letter["reviewed"]}" w:initials="{initials}">'
        f'<w:p>{run(c)}</w:p></w:comment>' for k, c in enumerate(notes)) + "</w:comments>")
    core = (f'{HEAD}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(letter["title"])}</dc:title><dc:creator>Larkspur &amp; Vance CPAs</dc:creator>'
            f'<cp:lastModifiedBy>{escape(partner)}</cp:lastModifiedBy>'
            '<dcterms:created xsi:type="dcterms:W3CDTF">2026-01-05T09:00:00Z</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{letter["reviewed"]}</dcterms:modified></cp:coreProperties>')
    has = bool(notes)
    types = (f'{HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             f'<Override PartName="/word/document.xml" ContentType="{MAIN}.document.main+xml"/>'
             + (f'<Override PartName="/word/comments.xml" ContentType="{MAIN}.comments+xml"/>' if has else "")
             + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
             "</Types>")
    rels = (f'{HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
            'Target="docProps/core.xml"/></Relationships>')
    doc_rels = (f'{HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + (f'<Relationship Id="rId1" Type="{REL}/comments" Target="comments.xml"/>' if has else "")
                + "</Relationships>")
    parts = [("[Content_Types].xml", types), ("_rels/.rels", rels), ("docProps/core.xml", core),
             ("word/_rels/document.xml.rels", doc_rels), ("word/document.xml", doc)]
    if has:
        parts.append(("word/comments.xml", comments))
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts:
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 1, 9, 0, 0))  # fixed: same bytes on every machine
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text)
    return buf.getvalue()


if __name__ == "__main__":
    data = json.loads((HERE / "letters.json").read_text(encoding="utf-8"))
    for letter in data["letters"]:
        path = HERE / (letter["file"] + ".docx")
        path.write_bytes(build(letter, data["partner"]))
        print(f"wrote {path.name} ({len(letter['comments'])} comment(s))")
