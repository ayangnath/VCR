#!/usr/bin/env python3
"""Render the toolbar icons from the popup's color-wheel concept (popup.css
.popup-icon): a 3-way conic split in Okabe-Ito blue/orange/green. Re-run
after changing the wheel colors or sizes.

Usage:
    python3 scripts/generate_icons.py
"""

from pathlib import Path
from PIL import Image, ImageDraw

# Okabe-Ito colorblind-safe palette, matching popup.css .popup-icon
BLUE = (0, 114, 178, 255)
ORANGE = (230, 159, 0, 255)
GREEN = (0, 158, 115, 255)

SIZES = (16, 48, 128)
SUPERSAMPLE = 8  # draw large, downscale for anti-aliasing
OUT_DIR = Path(__file__).resolve().parent.parent / "icons"


def render(size: int) -> Image.Image:
    big = size * SUPERSAMPLE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = big * 0.04
    bbox = (margin, margin, big - margin, big - margin)

    # CSS conic-gradient angles start at 12 o'clock (top) and increase
    # clockwise; PIL pieslice angles start at 3 o'clock and increase
    # clockwise, so subtract 90 degrees to align the two systems.
    draw.pieslice(bbox, -90, 30, fill=BLUE)     # 0-120deg: blue
    draw.pieslice(bbox, 30, 150, fill=ORANGE)   # 120-240deg: orange
    draw.pieslice(bbox, 150, 270, fill=GREEN)   # 240-360deg: green

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    for size in SIZES:
        out_path = OUT_DIR / f"icon{size}.png"
        render(size).save(out_path)
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
