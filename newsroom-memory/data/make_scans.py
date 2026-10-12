#!/usr/bin/env python3
"""
Regenerates data/records/*.pdf from story.json: each page is rendered as a
slightly crooked, speckled grey image and saved as an image-only PDF, the way a
records office's scanner would — there is no text layer to copy from.

You do not need to run this: the PDFs are checked in.  It needs Pillow
(`pip install pillow`) and a Courier New font; demo.py itself needs neither.
"""
import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "records"
FONTS = ["/System/Library/Fonts/Supplemental/Courier New.ttf", "/usr/share/fonts/truetype/msttcorefonts/Courier_New.ttf"]
BOLD = ["/System/Library/Fonts/Supplemental/Courier New Bold.ttf", "/usr/share/fonts/truetype/msttcorefonts/Courier_New_Bold.ttf"]


def font(paths, size):
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    sys.exit("Courier New not found; edit FONTS in make_scans.py")


def page(header, lines, seed):
    rnd = random.Random(seed)
    w, h = 1700, 2200                     # 8.5 x 11 in at 200 dpi
    im = Image.new("L", (w, h), 245)
    d = ImageDraw.Draw(im)
    d.text((150, 150), header, font=font(BOLD, 40), fill=20)
    body = font(FONTS, 34)
    y = 260
    for ln in lines:
        d.text((150, y), ln, font=body, fill=25)
        y += 52
    for _ in range(3000):                 # scanner speckle
        d.point((rnd.randrange(w), rnd.randrange(h)), fill=rnd.randrange(120, 200))
    im = im.rotate(rnd.uniform(-0.8, 0.8), fillcolor=245, resample=Image.BICUBIC)
    return im.filter(ImageFilter.GaussianBlur(0.6)).convert("RGB")


def main():
    story = json.loads((HERE / "story.json").read_text())
    OUT.mkdir(exist_ok=True)
    for i, rec in enumerate(story["records"]):
        imgs = [page(p["header"], p["lines"], seed=i * 10 + n) for n, p in enumerate(rec["pages"])]
        imgs[0].save(OUT / rec["file"], save_all=True, append_images=imgs[1:], resolution=200)
        print(OUT / rec["file"])


if __name__ == "__main__":
    main()
