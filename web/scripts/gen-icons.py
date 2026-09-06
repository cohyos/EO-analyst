"""Generate PWA/iOS icon PNGs from the favicon.svg design using Pillow only
(no cairosvg/rsvg available in this environment — the SVG is simple enough
to redraw as primitives at high resolution and downsample).

Source design (web/public/favicon.svg): 32x32 viewBox, rounded-rect
background #0E5C68 (radius 6/32 of the box), a ring circle #C4561B
(r=8/32, stroke 2.5/32) and a solid center dot #C4561B (r=2.5/32).

Outputs (web/public/):
  - icon-192.png            192x192, standard, fill covers the canvas edge-to-edge
  - icon-512.png            512x512, standard
  - icon-192-maskable.png   192x192, design shrunk to the ~80% "safe zone" for maskable
  - icon-512-maskable.png   512x512, maskable
  - apple-touch-icon.png    180x180, square (no rounding baked in — iOS applies its own mask)
"""

from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw

BG = (14, 92, 104, 255)  # #0E5C68
FG = (196, 86, 27, 255)  # #C4561B

OUT_DIR = Path(__file__).resolve().parent.parent / "public"

SUPERSAMPLE = 4  # draw big, downsample for clean anti-aliased edges


def draw_mark(size: int, *, rounded: bool, scale: float = 1.0) -> Image.Image:
    """Render the mark at `size`x`size`. `scale` < 1 shrinks the ring+dot
    toward the center (used for maskable safe-zone padding); the background
    always fills the full canvas edge-to-edge."""
    ss = size * SUPERSAMPLE
    img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if rounded:
        radius = ss * (6 / 32)
        draw.rounded_rectangle([0, 0, ss - 1, ss - 1], radius=radius, fill=BG)
    else:
        draw.rectangle([0, 0, ss - 1, ss - 1], fill=BG)

    cx = cy = ss / 2
    ring_r = ss * (8 / 32) * scale
    ring_w = max(1, ss * (2.5 / 32) * scale)
    dot_r = ss * (2.5 / 32) * scale

    draw.ellipse(
        [cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r],
        outline=FG,
        width=int(round(ring_w)),
    )
    draw.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=FG)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    draw_mark(192, rounded=True).save(OUT_DIR / "icon-192.png")
    draw_mark(512, rounded=True).save(OUT_DIR / "icon-512.png")

    # Maskable: background must run edge-to-edge (no rounding — the OS mask
    # does its own shape), with the ring+dot scaled down so nothing critical
    # falls outside the ~80% safe zone various masks (circle, squircle) crop to.
    draw_mark(192, rounded=False, scale=0.72).save(OUT_DIR / "icon-192-maskable.png")
    draw_mark(512, rounded=False, scale=0.72).save(OUT_DIR / "icon-512-maskable.png")

    # apple-touch-icon: square, edge-to-edge — iOS applies its own rounded-
    # square mask and drop shadow, a pre-rounded or transparent source looks
    # wrong once iOS composites it.
    draw_mark(180, rounded=False).save(OUT_DIR / "apple-touch-icon.png")

    print("Wrote:", ", ".join(p.name for p in OUT_DIR.glob("icon-*.png")), "apple-touch-icon.png")


if __name__ == "__main__":
    main()
