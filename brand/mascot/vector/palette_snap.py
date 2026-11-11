"""Snap every fill in a traced SVG to the nearest colour of the mashqgoh mascot palette and drop
background shapes (snapped to "bg" AND starting on the canvas edge), so the art sits on transparency.

Usage: python palette_snap.py <in.svg> <out.svg> [source.png]
With source.png, bg-like shapes off the canvas edge are dropped when they sit in the raster's
corner-connected background, and recoloured to light fur when they sit inside the character.
Exit codes: 3 = no filled shapes, 4 = a colour is farther than MAX_DIST from every palette entry.
Same approach as TulporGo's brand/mascot/directions/palette_snap.py.
"""
import re
import sys

MAX_DIST = 120.0
EDGE = 2.0

# (hex, role). Brand: emerald #0B7A5F, yellow #FFD23F, ink #10231C, white, bg #F2F7F4 (A · Zumrad).
# The rest are the character's own fur, spot and blush tones, fixed here so every export matches.
PALETTE = [
    ("#F2F7F4", "bg"),
    ("#FFFFFF", ""),       # sticker outline, paper, eye shine
    ("#F7EFE6", ""),       # fur, light cream
    ("#E6D6C6", ""),       # fur shade
    ("#C9B8A8", ""),       # fur deep shade
    ("#8C8079", ""),       # spot rim / mid grey
    ("#3B3634", ""),       # spots
    ("#10231C", ""),       # ink lines (brand)
    ("#0B7A5F", ""),       # emerald (brand): scarf, eyes
    ("#075C47", ""),       # emerald shade
    ("#3FA585", ""),       # emerald light / iris highlight
    ("#FFD23F", ""),       # yellow (brand): stripe, pencil
    ("#E0A800", ""),       # yellow shade
    ("#F3B4AE", ""),       # inner ear, blush
    ("#D9646C", ""),       # tongue, mouth
]

FILL = re.compile(r'fill:(#[0-9A-Fa-f]{6})')
PATH = re.compile(r'<(path|polygon|rect)\b[^>]*?/>', re.S)
VIEWBOX = re.compile(r'viewBox="0 0 ([\d.]+) ([\d.]+)"')
FIRST_POINT = re.compile(r'(?:\bd="M|points=")\s*(-?[\d.]+)[ ,]+(-?[\d.]+)')


def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def nearest(h):
    c = rgb(h)
    best = min(PALETTE, key=lambda p: sum((a - b) ** 2 for a, b in zip(c, rgb(p[0]))))
    dist = sum((a - b) ** 2 for a, b in zip(c, rgb(best[0]))) ** 0.5
    return best, dist


INTERIOR = "#F7EFE6"   # bg-snapped shapes inside the character are light fur, not holes
BG_TOL = 22            # flood-fill tolerance on the source PNG
SEAM = 0.8             # px, same-colour stroke that closes tracing seams


def bg_mask(png_path):
    """Pixels connected to the image corners within BG_TOL of the corner colour = outside the sticker."""
    from PIL import Image, ImageDraw
    im = Image.open(png_path).convert("RGB")
    w, h = im.size
    for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if im.getpixel(xy) != (255, 0, 255):
            ImageDraw.floodfill(im, xy, (255, 0, 255), thresh=BG_TOL)
    return lambda x, y: 0 <= x < w and 0 <= y < h and im.getpixel((int(x), int(y))) == (255, 0, 255)


def snap(svg: str, outside=None):
    """outside(x, y) -> True when the point is background in the source raster (see bg_mask)."""
    w, h = (float(x) for x in VIEWBOX.search(svg).groups())
    stats = {"shapes": 0, "dropped_bg": 0, "interior_bg": 0, "max_dist": 0.0, "far": set()}

    def repl(m):
        el = m.group(0)
        f = FILL.search(el)
        if not f:
            return el
        stats["shapes"] += 1
        (hexv, role), dist = nearest(f.group(1))
        stats["max_dist"] = max(stats["max_dist"], dist)
        if dist > MAX_DIST:
            stats["far"].add(f.group(1).upper())
        if role == "bg":
            p = FIRST_POINT.search(el)
            if p:
                x, y = float(p.group(1)), float(p.group(2))
                on_edge = x <= EDGE or y <= EDGE or x >= w - EDGE or y >= h - EDGE
                if on_edge or (outside is not None and outside(x, y)):
                    stats["dropped_bg"] += 1
                    return ""
            if outside is not None:
                stats["interior_bg"] += 1
                hexv = INTERIOR
        # A same-colour hairline stroke closes the sub-pixel seams that abutting tracing leaves
        # between shapes (they show as light lines on a dark background).
        return el[:f.start(1)] + hexv + f";stroke:{hexv};stroke-width:{SEAM}" + el[f.end(1):]

    out = PATH.sub(repl, svg)
    return out, stats


def main(argv):
    src, dst = argv[1], argv[2]
    mask = bg_mask(argv[3]) if len(argv) > 3 else None
    svg = open(src, encoding="latin-1").read()
    out, st = snap(svg, mask)
    if st["shapes"] == 0:
        print("no filled shapes")
        return 3
    used = sorted({m.upper() for m in FILL.findall(out)})
    open(dst, "w", encoding="utf-8").write(out.replace('encoding="iso-8859-1"', 'encoding="utf-8"'))
    print(f"shapes={st['shapes']} dropped_bg={st['dropped_bg']} interior_bg={st['interior_bg']} "
          f"colours={len(used)} max_dist={st['max_dist']:.1f}")
    if st["far"]:
        print("FAR:", " ".join(sorted(st["far"])))
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
