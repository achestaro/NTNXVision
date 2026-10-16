"""Generate the NTNXVision placeholder app icons under app/build/.

Brand mark: an iris (#5B3FD9) hexagon-eye on a dark (#0B131B) rounded tile.

    core/.venv/bin/python scripts/generate-icons.py      # needs Pillow (a core dependency)

Writes app/build/icon.png (1024), app/build/icons/<n>x<n>.png, app/build/icon.ico and,
on macOS (iconutil), app/build/icon.icns. Re-run after changing the art; commit outputs.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "build"
TILE = (0x0B, 0x13, 0x1B, 255)
IRIS = (0x5B, 0x3F, 0xD9, 255)
IRIS_LIGHT = (0x8E, 0x78, 0xF0, 255)
WHITE = (0xF2, 0xF0, 0xFF, 255)
SS = 4  # supersampling factor


def render(size: int = 1024) -> Image.Image:
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # macOS-style tile: ~9% inset, rounded corners.
    inset = int(s * 0.09)
    d.rounded_rectangle((inset, inset, s - inset, s - inset), radius=int(s * 0.19), fill=TILE)

    cx = cy = s / 2
    # Hexagon outline (pointy-top).
    r = s * 0.31
    hexagon = [
        (cx + r * math.cos(math.radians(90 + 60 * i)), cy + r * math.sin(math.radians(90 + 60 * i)))
        for i in range(6)
    ]
    d.polygon(hexagon, outline=IRIS, width=int(s * 0.035))

    # Eye: almond (lens) shape made of two circular arcs.
    w = s * 0.40
    h = s * 0.20
    big_r = (w * w / 4 + h * h / 4) / h
    top, bot = [], []
    for i in range(121):
        x = cx - w / 2 + w * i / 120
        dy = math.sqrt(max(big_r * big_r - (x - cx) ** 2, 0)) - (big_r - h / 2)
        top.append((x, cy - dy))
        bot.append((x, cy + dy))
    d.polygon(top + bot[::-1], fill=WHITE)

    # Iris, inner ring, pupil, highlight.
    ir = h * 0.46
    d.ellipse((cx - ir, cy - ir, cx + ir, cy + ir), fill=IRIS)
    ir2 = ir * 0.78
    d.ellipse((cx - ir2, cy - ir2, cx + ir2, cy + ir2), outline=IRIS_LIGHT, width=int(s * 0.006))
    pr = ir * 0.42
    d.ellipse((cx - pr, cy - pr, cx + pr, cy + pr), fill=TILE)
    hr = ir * 0.16
    hx, hy = cx + ir * 0.30, cy - ir * 0.32
    d.ellipse((hx - hr, hy - hr, hx + hr, hy + hr), fill=WHITE)

    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    master = render(1024)
    master.save(OUT / "icon.png")

    icons = OUT / "icons"
    icons.mkdir(exist_ok=True)
    for n in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
        master.resize((n, n), Image.LANCZOS).save(icons / f"{n}x{n}.png")

    master.save(
        OUT / "icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "icon.iconset"
            iconset.mkdir()
            for n in (16, 32, 128, 256, 512):
                master.resize((n, n), Image.LANCZOS).save(iconset / f"icon_{n}x{n}.png")
                master.resize((n * 2, n * 2), Image.LANCZOS).save(iconset / f"icon_{n}x{n}@2x.png")
            subprocess.run(
                ["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "icon.icns")], check=True
            )
    else:
        print("iconutil not found (not macOS): icon.icns left unchanged", file=sys.stderr)
    print(f"icons written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
