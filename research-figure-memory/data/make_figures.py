#!/usr/bin/env python3
"""Regenerate the survey figures in data/ from data/survey.json (needs Pillow; the demo itself does not).

For every figure it writes what the team's charting tool exports:

  fig-<x>.png        the bar chart with the value printed on every bar (the team pastes these into its notes)
  fig-<x>-plain.png  the same chart without the printed values

Both are 8-bit RGB PNGs, so demo.py can wrap them in a PDF without re-encoding. The Fernhill Institute and its
survey are fictional.

    pip install pillow && python3 data/make_figures.py
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FONTS = ["/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
COLOURS = [(70, 110, 180), (230, 140, 40)]


def font(size: int):
    for p in FONTS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()


def chart(fig: dict, path: Path, labelled: bool) -> None:
    W, H = 1100, 660
    im = Image.new("RGB", (W, H), "white")
    g = ImageDraw.Draw(im)
    f, ft = font(17), font(22)
    g.text((60, 18), fig["title"], fill="black", font=ft)
    x0, y0, x1, y1, ymax = 90, 90, 1060, 560, fig["ymax"]
    step = 10 if ymax > 30 else 5
    for t in range(0, ymax + 1, step):
        y = y1 - (y1 - y0) * t / ymax
        g.line((x0, y, x1, y), fill=(225, 225, 225))
        g.text((50, y - 10), str(t), fill="black", font=f)
    g.line((x0, y0, x0, y1), fill="black")
    g.line((x0, y1, x1, y1), fill="black")
    cats = list(fig["values"])
    n = len(fig["series"])
    gw = (x1 - x0) / len(cats)
    bw = (gw - 30) / n
    for i, c in enumerate(cats):
        for s, v in enumerate(fig["values"][c]):
            bx = x0 + i * gw + 15 + s * bw
            by = y1 - (y1 - y0) * v / ymax
            g.rectangle((bx, by, bx + bw - 4, y1), fill=COLOURS[s])
            if labelled:
                g.text((bx + 4, by - 22), f"{v:.1f}", fill="black", font=f)
        g.text((x0 + i * gw + 12, y1 + 10), c, fill="black", font=f)
    if n > 1:
        for s, name in enumerate(fig["series"]):
            g.rectangle((700 + s * 180, 60, 716 + s * 180, 76), fill=COLOURS[s])
            g.text((722 + s * 180, 58), name, fill="black", font=f)
    im.save(path)  # 8-bit RGB, non-interlaced


def main() -> None:
    d = json.loads((HERE / "survey.json").read_text(encoding="utf-8"))
    for fig in d["figures"]:
        chart(fig, HERE / f"{fig['file']}.png", True)
        chart(fig, HERE / f"{fig['file']}-plain.png", False)
        print(f"wrote {fig['file']}.png, {fig['file']}-plain.png")


if __name__ == "__main__":
    main()
