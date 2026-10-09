#!/usr/bin/env python3
"""Regenerate the January 2026 employee handbook PDF in library/handbook/.

The committed PDF is what the demo uploads; you only need this if you edit the text below.
Unlike demo.py it needs one third-party package:  pip install reportlab
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

HERE = Path(__file__).resolve().parent
TARGET = HERE / "library" / "handbook" / "employee-handbook-2026-01.pdf"
STYLES = getSampleStyleSheet()

TITLE = "Fernhill Robotics — Employee Handbook (January 2026 edition)"
BYLINE = ("Owner: People team, Fernhill Robotics. Approved by Dana Whitfield, Head of People, on 2025-12-10. "
          "Demo data — Fernhill Robotics is fictional.")
SECTIONS = [
    ("1. Welcome",
     "Fernhill Robotics builds autonomous floor-cleaning robots for hospitals and airports. About 400 people work "
     "across the Denver headquarters, the Boston lab and remotely."),
    ("4.2 Parental leave",
     "The primary caregiver receives 16 weeks of fully paid leave and the secondary caregiver 4 weeks, after "
     "6 months of service. Tell your manager and the People team at least 8 weeks before the leave starts."),
    ("5.1 Paid time off",
     "Employees receive 20 days of paid vacation per calendar year, prorated in the first year. Up to 5 unused "
     "days carry over to the next year."),
    ("6.1 Travel meal allowance",
     "When travelling for work you may spend up to USD 50 per travel day on meals. No receipts are needed."),
    ("7.3 Home-office stipend",
     "New employees may claim a one-off USD 500 stipend for home-office equipment within their first 90 days."),
    ("8. Your first day",
     "Your manager and the People team send you a role-specific first-day plan before you start."),
]


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(TARGET), pagesize=A4, title=TITLE, author="Fernhill Robotics People team (demo data)")
    story = [Paragraph(TITLE, STYLES["Title"]), Paragraph(BYLINE, STYLES["Italic"]), Spacer(1, 12)]
    for heading, body in SECTIONS:
        story += [Paragraph(heading, STYLES["Heading2"]), Paragraph(body, STYLES["BodyText"]), Spacer(1, 6)]
    doc.build(story)
    print(f"wrote {TARGET.relative_to(HERE)}")


if __name__ == "__main__":
    main()
