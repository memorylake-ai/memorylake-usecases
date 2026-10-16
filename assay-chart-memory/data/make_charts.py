#!/usr/bin/env python3
"""Regenerate the plate-run files in data/ from data/assay.json (needs Pillow; the demo itself does not).

For every run it writes what the plate reader's software exports:

  ka7-ic50-<run>.png           the IC50 bar chart, as the lab saves it into its notebook
  ka7-ic50-<run>-labelled.png  the same chart with the value printed on every bar
  ka7-ic50-<run>.csv           the numbers behind the chart

Larkspur Bio and its compounds are fictional.

    pip install pillow && python3 data/make_charts.py
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FONTS = ["/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def font(size: int):
    for p in FONTS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()


def chart(d: dict, run: dict, path: Path, labelled: bool) -> None:
    W, H = 1000, 640
    im = Image.new("RGB", (W, H), "white")
    g = ImageDraw.Draw(im)
    f, ft = font(18), font(24)
    g.text((60, 20), f"IC50 by compound - {d['assay_name']}, plate run {run['run']}", fill="black", font=ft)
    x0, y0, x1, y1, ymax = 100, 80, 960, 560, 250
    for t in range(0, ymax + 1, 50):
        y = y1 - (y1 - y0) * t / ymax
        g.line((x0, y, x1, y), fill=(220, 220, 220))
        g.text((50, y - 10), str(t), fill="black", font=f)
    g.line((x0, y0, x0, y1), fill="black")
    g.line((x0, y1, x1, y1), fill="black")
    g.text((10, 300), d["unit"], fill="black", font=f)
    bw = (x1 - x0) / len(d["compounds"])
    for i, c in enumerate(d["compounds"]):
        v = run["ic50"][c]
        bx, by = x0 + i * bw + 15, y1 - (y1 - y0) * v / ymax
        g.rectangle((bx, by, bx + bw - 30, y1), fill=(70, 110, 180))
        g.text((bx - 2, y1 + 8), c, fill="black", font=f)
        if labelled:
            g.text((bx + 10, by - 24), f"{v:.1f}", fill="black", font=f)
    im.save(path, optimize=True)


def csv(d: dict, run: dict, path: Path) -> None:
    lines = [f"Assay,Plate,Run date,Compound,IC50 ({d['unit']})"]
    lines += [f"{d['assay']},{run['plate']},{run['run']},{c},{run['ic50'][c]:.1f}" for c in d["compounds"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    d = json.loads((HERE / "assay.json").read_text(encoding="utf-8"))
    for run in d["runs"]:
        stem = f"{d['assay'].lower().replace('-', '')}-ic50-{run['run']}"
        chart(d, run, HERE / f"{stem}.png", False)
        chart(d, run, HERE / f"{stem}-labelled.png", True)
        csv(d, run, HERE / f"{stem}.csv")
        print(f"wrote {stem}.png, {stem}-labelled.png, {stem}.csv")


if __name__ == "__main__":
    main()
