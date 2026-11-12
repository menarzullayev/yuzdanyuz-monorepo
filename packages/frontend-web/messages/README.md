# Message catalogs

| File | Status |
|---|---|
| `uz.json` | Source of truth (Uzbek Latin). Use U+02BB (ʻ) in oʻ/gʻ and U+02BC (ʼ) as the apostrophe, never ASCII `'` (it is the ICU escape character). |
| `ru.json`, `en.json` | Full translations. |
| `kaa.json` | **Needs native review.** Machine-assisted Karakalpak (Latin, 2016 alphabet). Words flagged as uncertain: `akkaunt`, `qádem`, `baǵdarı`, `ıqtıyarlı`, `Sálem`, `Qaraqalpaq tili`. A native speaker must proofread before launch. |
| `uz-Cyrl.json` | **Generated** from `uz.json`. Do not edit by hand. |
| `uz-yangi.json` | **Generated** from `uz.json` (new Uzbek alphabet: oʻ→ö, gʻ→ğ, sh→ş, ch→ç; tutuq ʼ stays). Do not edit by hand. |

Regenerate after any change to `uz.json`:

```bash
npm run i18n:generate    # scripts/generate-locales.mjs, rules in scripts/translit.mjs
npm test                 # fails if a generated file drifted from uz.json
```

Transliteration rules (see `tests/translit.test.ts`):

* Text inside ICU `{...}` and tags `<...>` is never transliterated.
* New alphabet: `oʻ gʻ sh ch` → `ö ğ ş ç` (case follows the first letter). `sʼh` is not a digraph, so it stays `s` + `ʼ` + `h`.
* Cyrillic: `oʻ→ў gʻ→ғ sh→ш ch→ч h→ҳ q→қ x→х`; `ya yu yo ye → я ю ё е` (but `yoʻ → йў`);
  `e → э` at the start of a word or after a vowel, `е` elsewhere; `ts → ц` only in loanword stems
  (`-tsiya`, `tsentr`, ...), native `ts` (`otsiz`) stays `тс`; `ʼ → ъ`.
  Exam acronyms and brand names listed in `KEEP_LATIN` stay in Latin script.

Locale routing: `uz` has no URL prefix; the rest use `/ru`, `/en`, `/kaa`, `/uz-Cyrl`, `/uz-yangi`.
`<html lang>` for the new alphabet is the private-use tag `uz-Latn-x-yangi`.

TODO(2027-04-22): the new alphabet becomes the default on that date. Switch `DEFAULT_LOCALE`
in `src/i18n/locales.ts` then (not before) and keep `uz` reachable under `/uz`.
