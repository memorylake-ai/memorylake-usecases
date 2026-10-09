#!/usr/bin/env python3
"""Regenerate the contract playbook PDF in library/playbook/.

The committed PDF is what the demo uploads; you only need this if you edit the text below.
Unlike demo.py it needs one third-party package:  pip install reportlab
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

HERE = Path(__file__).resolve().parent
TARGET = HERE / "library" / "playbook" / "harbrook-contract-playbook-2026.pdf"
STYLES = getSampleStyleSheet()

TITLE = "Harbrook Logistics — Commercial Contract Playbook (2026 edition)"
BYLINE = "Owner: Legal, Harbrook Logistics. Approved by the General Counsel on 2026-01-15. Supersedes the 2024 edition."
SECTIONS = [
    ("1. Limitation of liability",
     "Standard position: each party's total liability is capped at 12 months of fees paid under the agreement. "
     "Any cap above 12 months needs written sign-off from the General Counsel. Exclusions from the cap: "
     "fraud, wilful misconduct, and breach of confidentiality. Never accept an uncapped liability for Harbrook."),
    ("2. Payment terms",
     "Standard position: net 45 days from the date of a correct invoice. Net 30 is an acceptable fallback only in "
     "exchange for an early-payment discount of at least 2 percent. Never accept terms shorter than net 30."),
    ("3. Indemnification",
     "Standard position: indemnities are mutual and limited to third-party intellectual-property infringement "
     "claims and gross negligence. Reject one-way indemnities in the supplier's favour."),
    ("4. Intellectual property",
     "Standard position: Harbrook owns all deliverables and work product created for it, and all Harbrook data. "
     "A software vendor may keep ownership of its pre-existing platform, provided Harbrook data and reports "
     "generated from it remain Harbrook's."),
    ("5. Term and renewal",
     "Standard position: initial term of up to three years; renewal must be an express written decision, "
     "never automatic. Notice of non-renewal: 60 days."),
    ("6. Escalation",
     "Deviations that exceed a previously accepted precedent with the same counterparty go to the General "
     "Counsel with the precedent attached. Reviewers record every accepted exception against the counterparty."),
]


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(TARGET), pagesize=A4, title=TITLE, author="Harbrook Logistics Legal (demo data)")
    story = [Paragraph(TITLE, STYLES["Title"]), Paragraph(BYLINE, STYLES["Italic"]), Spacer(1, 12)]
    for heading, body in SECTIONS:
        story += [Paragraph(heading, STYLES["Heading2"]), Paragraph(body, STYLES["BodyText"]), Spacer(1, 6)]
    doc.build(story)
    print(f"wrote {TARGET.relative_to(HERE)}")


if __name__ == "__main__":
    main()
