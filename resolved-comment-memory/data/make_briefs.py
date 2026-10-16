#!/usr/bin/env python3
"""Writes the three client briefs in briefs.json as Word documents (.docx), standard library only.

Two of them carry review comment threads the way Word stores them: the comment text in word/comments.xml, a marker
around the line it is on in word/document.xml, and in word/commentsExtended.xml one entry per comment with
w15:done="1" when the thread was marked resolved. Resolving a thread in Word changes only that flag: the comment
stays in the file, greyed out in the comment pane. The demo itself only reads the .docx files; run this again only
if you change briefs.json."""
import json
import zipfile
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
      'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"')
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
RPR = '<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr>'
MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
EXT_REL = "http://schemas.microsoft.com/office/2011/relationships/commentsExtended"


def run(text: str) -> str:
    return f'<w:r>{RPR}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def build(brief: dict, people: dict) -> bytes:
    paras = ['<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>' + run(brief["title"]) + "</w:p>"]
    comments, ext = [], []
    for label, text in brief["lines"]:
        body = run(f"{label}: {text}")
        for t in [t for t in brief["threads"] if t["on"] == label]:
            k = len(comments)
            pid = f"{0x1A000000 + k + 1:08X}"
            author = people[t["by"]].split(" (")[0]
            comments.append(f'<w:comment w:id="{k}" w:author="{escape(author)}" w:date="{brief["saved"]}T09:00:00Z" '
                            f'w:initials="{t["by"]}"><w:p w14:paraId="{pid}" w14:textId="77777777">{run(t["text"])}</w:p></w:comment>')
            ext.append(f'<w15:commentEx w15:paraId="{pid}" w15:done="{1 if t["state"] == "resolved" else 0}"/>')
            body = (f'<w:commentRangeStart w:id="{k}"/>{body}<w:commentRangeEnd w:id="{k}"/>'
                    f'<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr><w:commentReference w:id="{k}"/></w:r>')
        paras.append(f"<w:p>{body}</w:p>")
    doc = (f'{HEAD}<w:document {NS}><w:body>{"".join(paras)}'
           '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr></w:body></w:document>')
    core = (f'{HEAD}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(brief["title"])}</dc:title><dc:creator>Halfmoon Studio</dc:creator>'
            '<cp:lastModifiedBy>Halfmoon Studio</cp:lastModifiedBy>'
            '<dcterms:created xsi:type="dcterms:W3CDTF">2026-08-01T09:00:00Z</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{brief["saved"]}T09:00:00Z</dcterms:modified></cp:coreProperties>')
    has = bool(comments)
    types = (f'{HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             f'<Override PartName="/word/document.xml" ContentType="{MAIN}.document.main+xml"/>'
             + (f'<Override PartName="/word/comments.xml" ContentType="{MAIN}.comments+xml"/>'
                f'<Override PartName="/word/commentsExtended.xml" ContentType="{MAIN}.commentsExtended+xml"/>' if has else "")
             + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
             "</Types>")
    rels = (f'{HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
            'Target="docProps/core.xml"/></Relationships>')
    doc_rels = (f'{HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + (f'<Relationship Id="rId1" Type="{REL}/comments" Target="comments.xml"/>'
                   f'<Relationship Id="rId2" Type="{EXT_REL}" Target="commentsExtended.xml"/>' if has else "")
                + "</Relationships>")
    parts = [("[Content_Types].xml", types), ("_rels/.rels", rels), ("docProps/core.xml", core),
             ("word/_rels/document.xml.rels", doc_rels), ("word/document.xml", doc)]
    if has:
        parts.append(("word/comments.xml", f'{HEAD}<w:comments {NS}>{"".join(comments)}</w:comments>'))
        parts.append(("word/commentsExtended.xml", f'{HEAD}<w15:commentsEx {NS}>{"".join(ext)}</w15:commentsEx>'))
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts:
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 1, 9, 0, 0))  # fixed: same bytes on every machine
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text)
    return buf.getvalue()


if __name__ == "__main__":
    data = json.loads((HERE / "briefs.json").read_text(encoding="utf-8"))
    for brief in data["briefs"]:
        path = HERE / (brief["file"] + ".docx")
        path.write_bytes(build(brief, data["people"]))
        n = len(brief["threads"])
        r = sum(t["state"] == "resolved" for t in brief["threads"])
        print(f"wrote {path.name} ({n} comment thread(s), {r} resolved)")
