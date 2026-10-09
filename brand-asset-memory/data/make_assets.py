#!/usr/bin/env python3
"""Regenerate the six brand images in data/assets/ (needs Pillow; the demo itself does not).

They stand in for a DAM export: real brand files usually arrive with camera or export names
(IMG_2031.png) that say nothing about what is in them. Fernway Coffee Roasters is fictional.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "assets"
FERN, KILN, OAT, ROAST = "#2B5D4F", "#C8553D", "#F4EFE6", "#4A3428"
SANS = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
SERIF = "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"


def font(size: int, path: str = SANS) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def mark(d: ImageDraw.ImageDraw, x: int, y: int, r: int, fill: str = FERN, leaf: str = OAT) -> None:
    """The green circle with a fern frond in it."""
    d.ellipse((x - r, y - r, x + r, y + r), fill=fill)
    d.line((x, y + r * 0.7, x, y - r * 0.7), fill=leaf, width=max(3, r // 12))
    for i in range(5):
        yy = y + r * 0.45 - i * r * 0.25
        span = r * (0.5 - i * 0.07)
        d.line((x, yy, x - span, yy - r * 0.18), fill=leaf, width=max(2, r // 16))
        d.line((x, yy, x + span, yy - r * 0.18), fill=leaf, width=max(2, r // 16))


def logo(d: ImageDraw.ImageDraw, x: int, y: int, scale: float = 1.0, color: str = FERN, sub: str = KILN) -> None:
    mark(d, int(x + 90 * scale), int(y + 90 * scale), int(90 * scale), fill=color)
    d.text((x + 210 * scale, y + 20 * scale), "FERNWAY", font=font(int(92 * scale)), fill=color)
    d.text((x + 216 * scale, y + 122 * scale), "coffee roasters", font=font(int(36 * scale)), fill=sub)


def primary_logo() -> None:
    im = Image.new("RGB", (1000, 500), OAT)
    logo(ImageDraw.Draw(im), 110, 160)
    im.save(OUT / "IMG_2031.png")


def palette() -> None:
    im = Image.new("RGB", (1200, 420), "white")
    d = ImageDraw.Draw(im)
    for i, (hexv, name, use) in enumerate([(FERN, "Fern Green", "primary"), (KILN, "Kiln Red", "accent only"),
                                           (OAT, "Oat", "backgrounds"), (ROAST, "Roast Brown", "body text")]):
        x = 40 + i * 290
        d.rectangle((x, 40, x + 260, 240), fill=hexv, outline="#222222", width=2)
        d.text((x, 258), name, font=font(32), fill="#111111")
        d.text((x, 302), hexv, font=font(30), fill="#111111")
        d.text((x, 346), use, font=font(24), fill="#555555")
    im.save(OUT / "IMG_2047.png")


def misuse() -> None:
    im = Image.new("RGB", (1500, 560), "white")
    d = ImageDraw.Draw(im)
    d.text((40, 24), "Logo misuse - DON'T", font=font(44), fill="#C00000")
    panels = []
    # 1 stretched
    p = Image.new("RGB", (900, 260), "white"); logo(ImageDraw.Draw(p), 20, 40, 0.8); panels.append(p.resize((440, 300)))
    # 2 recolored
    p = Image.new("RGB", (900, 260), "white"); logo(ImageDraw.Draw(p), 20, 40, 0.8, color="#7B2CBF", sub="#F72585"); panels.append(p.resize((440, 300)))
    # 3 on a busy background
    p = Image.new("RGB", (900, 260), "white"); dp = ImageDraw.Draw(p)
    for k in range(0, 900, 30):
        dp.rectangle((k, 0, k + 15, 260), fill="#E0A458")
    logo(dp, 20, 40, 0.8); panels.append(p.resize((440, 300)))
    labels = ["Don't stretch it", "Don't recolor it", "Don't place it on busy patterns"]
    for i, (pn, lab) in enumerate(zip(panels, labels)):
        x = 40 + i * 480
        im.paste(pn, (x, 110))
        d.line((x, 110, x + 440, 410), fill="#E00000", width=12)
        d.line((x + 440, 110, x, 410), fill="#E00000", width=12)
        d.text((x, 430), lab, font=font(28), fill="#111111")
    im.save(OUT / "IMG_2052.png")


def harvest_bag() -> None:
    im = Image.new("RGB", (800, 1000), "#E9E2D3")
    d = ImageDraw.Draw(im)
    d.polygon([(200, 140), (600, 140), (640, 930), (160, 930)], fill=FERN)
    d.rectangle((250, 330, 550, 700), fill=OAT)
    mark(d, 400, 400, 46)
    d.text((300, 465), "FERNWAY", font=font(46), fill=FERN)
    d.text((300, 540), "Harvest", font=font(54), fill=KILN)
    d.text((330, 605), "Blend", font=font(54), fill=KILN)
    d.text((270, 780), "340 g  whole bean", font=font(32), fill=OAT)
    d.text((290, 830), "Autumn 2026", font=font(32), fill=OAT)
    im.save(OUT / "IMG_2088.jpg", quality=90)


def social_template() -> None:
    im = Image.new("RGB", (1080, 1080), FERN)
    d = ImageDraw.Draw(im)
    for k in range(60, 1020, 24):  # dashed safe zone
        d.line((k, 60, k + 12, 60), fill=OAT, width=3); d.line((k, 1020, k + 12, 1020), fill=OAT, width=3)
        d.line((60, k, 60, k + 12), fill=OAT, width=3); d.line((1020, k, 1020, k + 12), fill=OAT, width=3)
    d.text((110, 120), "safe zone: keep text inside the dashed line", font=font(26), fill=OAT)
    d.text((110, 380), "NEW", font=font(170), fill=OAT)
    d.text((110, 560), "ROAST", font=font(170), fill=KILN)
    d.text((110, 800), "Headline in Oat, one word in Kiln Red", font=font(34), fill=OAT)
    mark(d, 900, 900, 60, fill=OAT, leaf=FERN)
    d.text((110, 940), "@fernwayroasters", font=font(34), fill=OAT)
    im.save(OUT / "IMG_2093.png")


def old_wordmark() -> None:
    im = Image.new("RGB", (1000, 460), "#FFF8EC")
    d = ImageDraw.Draw(im)
    d.rectangle((60, 60, 940, 400), outline=ROAST, width=6)
    d.text((140, 120), "Fernway & Co.", font=font(104, SERIF), fill=ROAST)
    d.text((330, 270), "~ est. 2019 ~", font=font(44, SERIF), fill=ROAST)
    im.save(OUT / "IMG_1960.png")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for make in (primary_logo, palette, misuse, harvest_bag, social_template, old_wordmark):
        make()
    print("\n".join(sorted(p.name for p in OUT.iterdir())))
