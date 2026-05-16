# Design Reference — YuzdanYuz

> Source-of-truth: Claude Design canvas + extracted tokens.
> Implementation: `packages/frontend-web/` + `packages/frontend-mobile/`

---

## Folder map

```
docs/design/
├── README.md                          ← bu fayl (index)
├── claude-design-export/              ← Claude Design'dan RAW export
│   ├── screenshots/                   ← PNG snapshots (canvas page'lar)
│   ├── jsx/                           ← Claude Design JSX (REFERENCE ONLY, production code emas)
│   └── pdfs/                          ← To'liq PDF export (full design document)
├── tokens/                            ← Extracted design tokens (Tailwind config uchun)
│   ├── colors.json                    ← Color palette (light + dark)
│   ├── typography.json                ← Font scale, weights
│   ├── spacing.json                   ← Spacing/radius/shadow scale
│   └── tokens.md                      ← Human-readable description
├── screens/                           ← Per-screen reference + behavioral notes
│   ├── auth/                          ← login, OTP, signup
│   ├── student/                       ← dashboard, exam, results, leaderboard
│   ├── b2b/                           ← org admin, analytics
│   └── marketing/                     ← landing, pricing
└── components/                        ← Per-component spec
    ├── primitives/                    ← Button, Input, Card, etc. (shadcn parity)
    └── domain/                        ← ExamCard, StreakFlame, ScoreGauge, etc.
```

---

## Workflow

### Claude Design'da yangi iteratsiya tugaganda:

1. **Export PDF** — Claude Design → `Export as PDF`
   → save to `docs/design/claude-design-export/pdfs/YYYY-MM-DD-design-system-vN.pdf`

2. **Screenshots** — har asosiy canvas page (design system, screens, components):
   - PNG yoki JPG screenshot oling
   - save to `docs/design/claude-design-export/screenshots/<page-name>.png`
   - Filename convention: `01-tokens-colors.png`, `02-tokens-typography.png`, `10-screen-student-dashboard.png`, etc.

3. **JSX components** (agar download qilsangiz):
   - save to `docs/design/claude-design-export/jsx/`
   - **EHTIYOT**: bu fayllar production code EMAS — faqat reference.
     Real implementation `packages/frontend-web/src/components/` da yoziladi (shadcn/ui asosida).

4. **Tokens extraction** (manual yoki helper script bilan):
   - Color palette → `tokens/colors.json`
   - Typography scale → `tokens/typography.json`
   - Bu fayllar `packages/frontend-web/tailwind.config.ts` ga export qilinadi.

---

## Naming convention

```
01-tokens-colors.png             ← Design tokens
02-tokens-typography.png
03-tokens-spacing.png

10-primitives-buttons.png        ← shadcn-parity components
11-primitives-inputs.png
12-primitives-cards.png
...

20-domain-examcard.png           ← YuzdanYuz domain-specific
21-domain-streakflame.png
22-domain-scoregauge.png
...

30-screen-auth-login.png         ← Full screens
31-screen-auth-otp.png
32-screen-student-dashboard.png
33-screen-student-exam-take.png
34-screen-student-results.png
35-screen-student-leaderboard.png
36-screen-b2b-dashboard.png
37-screen-b2b-analytics.png
38-screen-marketing-landing.png
...

90-mobile-* va 91-mobile-*       ← Mobile variants (React Native uchun)

99-darkmode-*                    ← Dark mode variants
```

---

## Claude Code chat'ga ulashuv

Sizning hozirgi chat'imda (Claude Code session) men `Read` tool orqali bu fayllarga kira olaman:

```
- docs/design/README.md
- docs/design/claude-design-export/screenshots/*.png  (Read tool image support)
- docs/design/tokens/*.json
- docs/design/screens/*/*.md
```

Workflow:
1. Siz Claude Design natijasini bu yerga upload qilasiz
2. Menga "design'ni ko'r" deysiz
3. Men screenshot'larni o'qib feedback beraman (tokens to'g'rimi, components yetarli, gap'lar)
4. Birgalikda iterate qilamiz
5. Tugagandan keyin `packages/frontend-web/` scaffolding'ni boshlaymiz

---

## Versioning

Har major iteration uchun PDF + screenshot batch'ini sana bilan saqlang:
- `pdfs/2026-05-16-design-system-v1.pdf` (1-iteratsiya)
- `pdfs/2026-05-18-design-system-v2.pdf` (feedback'dan keyin)
- etc.

Eski version'lar'ni o'chirmang — refer back qilish foydali.
