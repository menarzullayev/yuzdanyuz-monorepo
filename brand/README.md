# Brand assets

The brand name is a single setting (`PROJECT_BRAND_NAME`); nothing here hard-codes it.

## Palette ("A · Zumrad", 2026-11-12)

| Role | Hex |
|---|---|
| Main (emerald) | `#0B7A5F` |
| Accent (yellow) | `#FFD23F` |
| Ink | `#10231C` |
| Background | `#F2F7F4` |
| Border | `#C9DCD3` |

Fonts: Unbounded 800 for headings, Manrope for text (both cover Ö Ğ Ş Ç of the new Uzbek alphabet).

## Mascot: snow leopard cub

| What | Where |
|---|---|
| Round-1 directions (8) and their prompts | `mascot/PROMPTS.md`, `mascot/directions/*.txt`, `mascot/directions/gallery.webp` |
| Model sheet (approved) | `mascot/vector/01-model-sheet-v1-brand.svg`, dark check `mascot/vector/model-sheet-brand-dark.webp` |
| Main pose | `mascot/vector/01-qor-qoploni-brand.svg`, check on 3 backgrounds `mascot/vector/check-3bg.webp` |
| 18 poses and where each is used | `mascot/poses/INDEX.md`, vectors `mascot/vector/poses/*-brand.svg`, dark check `mascot/vector/poses-brand-dark.webp` |

Pipeline (same as TulporGo): ChatGPT image → Illustrator Image Trace (`mascot/vector/trace.jsx`,
`trace_poses.jsx`, run through the Illustrator COM object) → `palette_snap.py` (snaps every fill to the
15-colour mascot palette, drops the background using a flood-fill mask of the source PNG, closes trace
seams with a same-colour hairline stroke) → visual check on light, dark and transparent backgrounds.

```bash
cd brand/mascot/vector && python -m pytest test_palette_snap.py -q
```

The mascot is AI-generated (ChatGPT). Before registering it as a trademark, check ownership and
registrability with a lawyer, the same way as TulporGo's `HUQUQIY-TEKSHIRUV.md`.
