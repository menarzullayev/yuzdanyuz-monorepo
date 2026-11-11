"""Tests for palette_snap.py, including the negative cases it must catch.

    python -m pytest test_palette_snap.py -q
"""
import palette_snap as ps

HEAD = '<svg viewBox="0 0 100 100">'


def svg(*paths):
    return HEAD + "".join(paths) + "</svg>"


def test_near_colour_snaps_to_brand_emerald():
    out, st = ps.snap(svg('<path style="fill:#0C7B60;" d="M50,50L60,60z"/>'))
    assert "fill:#0B7A5F" in out and st["max_dist"] < 3


def test_background_on_edge_is_dropped_but_interior_light_shape_is_kept():
    out, st = ps.snap(svg('<path style="fill:#EDF4F1;" d="M0,0L100,0z"/>',
                          '<path style="fill:#F1F6F3;" d="M40,40L50,50z"/>'))
    assert st["dropped_bg"] == 1
    assert out.count("<path") == 1 and 'M40,40' in out


def test_with_mask_speck_outside_is_dropped_and_seam_inside_becomes_fur():
    # Regression (2026-11-12): on a dark background, bg-coloured specks outside the sticker and
    # light seams across the fur showed up, because only edge-starting bg shapes were dropped.
    outside = lambda x, y: x > 80   # right strip is background in the "raster"
    out, st = ps.snap(svg('<path style="fill:#EEF5F2;" d="M90,50L95,55z"/>',
                          '<path style="fill:#EEF5F2;" d="M30,30L35,35z"/>'), outside)
    assert st["dropped_bg"] == 1 and st["interior_bg"] == 1
    assert "M90,50" not in out and 'fill:%s' % ps.INTERIOR in out and "fill:#F2F7F4" not in out


def test_every_kept_shape_gets_a_same_colour_seam_stroke():
    # Regression: hairline seams between abutting traced shapes showed on dark backgrounds.
    out, _ = ps.snap(svg('<path style="fill:#0C7B60;" d="M50,50L60,60z"/>'))
    assert "fill:#0B7A5F;stroke:#0B7A5F;stroke-width:%s" % ps.SEAM in out


def test_far_colour_is_reported_not_silently_snapped():
    # Pure blue is nowhere near the palette: must be flagged (exit code 4 in main()).
    _, st = ps.snap(svg('<path style="fill:#0000FF;" d="M50,50L60,60z"/>'))
    assert "#0000FF" in st["far"]


def test_output_uses_only_palette_colours():
    out, _ = ps.snap(svg('<path style="fill:#FFD43A;" d="M50,50z"/>', '<path style="fill:#121F1B;" d="M10,10z"/>'))
    pal = {p[0] for p in ps.PALETTE}
    assert set(ps.FILL.findall(out)) <= pal
