"""
DOM Obfuscation — CSS Shuffle.

Strategiya:
  - HTML ichidagi top-level elementlar (DIV, P, LI, SPAN) tartibsiz qilinadi.
  - Har birining `style="order: N"` atributi (flex order) bilan asl tartibda ko'rsatiladi.
  - Wrapper container `display: flex; flex-direction: column` ga o'rnatiladi.

Natija:
  - Foydalanuvchi to'g'ri tartibda ko'radi.
  - Scraper / DOM parser tartibi shuffled — pattern matching foydasiz.
  - Per-request: har so'rovda yangi random seed.

Cheklovlar:
  - Faqat top-level elementlar shuffle qilinadi (nested ichkarisi tegmaydi).
  - <table>, <ol>, <ul> kabi semantik tartib muhim bo'lganlari uchun ehtiyot kerak.
"""

from __future__ import annotations

import random
import re

from django.utils.safestring import mark_safe


# Top-level block-level tag'larni topish uchun regex.
# Faqat ichida tag bo'lmagan, balanced bo'lmagan tag'larga ehtiyot —
# shu uchun HTMLParser ishlatamiz.

import html.parser


class _TopLevelSplitter(html.parser.HTMLParser):
    """
    HTML stream'idan top-level elementlarni ajratib oladi.
    Whitespace va text tugunlari ham preserve qilinadi.
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.depth = 0
        self.parts: list[str] = []
        self._current: list[str] = []

    # ── Stream callbacks ────────────────────────────────────────

    def handle_starttag(self, tag, attrs):
        if self.depth == 0:
            self._flush_text_part()
        self._current.append(self.get_starttag_text() or '')
        self.depth += 1

    def handle_startendtag(self, tag, attrs):
        # Self-closing <br/>, <img/> kabi
        if self.depth == 0:
            self._flush_text_part()
            self.parts.append(self.get_starttag_text() or '')
        else:
            self._current.append(self.get_starttag_text() or '')

    def handle_endtag(self, tag):
        self.depth -= 1
        self._current.append(f'</{tag}>')
        if self.depth == 0:
            self.parts.append(''.join(self._current))
            self._current = []

    def handle_data(self, data):
        if self.depth == 0:
            # Top-level matn — alohida part sifatida saqlash (shuffle qilinmaydi)
            self._current.append(data)
            if self._current and self._current[-1].strip():
                # Faqat bo'sh bo'lmagan matn part bo'ladi
                pass
        else:
            self._current.append(data)

    def handle_entityref(self, name):
        target = self._current
        target.append(f'&{name};')

    def handle_charref(self, name):
        target = self._current
        target.append(f'&#{name};')

    # ── Helper ──────────────────────────────────────────────────

    def _flush_text_part(self):
        if self._current:
            buf = ''.join(self._current)
            if buf.strip():
                self.parts.append(buf)
            self._current = []

    def finalize(self) -> list[str]:
        if self._current:
            self._flush_text_part()
        return [p for p in self.parts if p.strip()]


# ── Asosiy API ────────────────────────────────────────────────

# Style attribute'ni element start tag'ga inject qilish uchun regex.
_TAG_START_RE = re.compile(r'^(<\s*[a-zA-Z][\w-]*)(\s|>|/>)')


def shuffle_html(html_str: str, seed: int | None = None) -> str:
    """
    HTML satridagi top-level block elementlarni shuffle qiladi va
    flex `order` orqali to'g'ri tartibda ko'rsatadi.

    Args:
        html_str: input HTML (templatedan keyin render qilingan)
        seed:     test repeatability uchun (default: random)

    Returns:
        Wrapped HTML string.
    """
    parts = _split_top_level(html_str)

    # 0 yoki 1 ta element — shuffle ma'no bermaydi
    if len(parts) <= 1:
        return html_str

    rng = random.Random(seed)
    indexed = list(enumerate(parts))
    rng.shuffle(indexed)

    out_parts = []
    for orig_idx, part in indexed:
        out_parts.append(_inject_order_style(part, orig_idx))

    inner = ''.join(out_parts)
    return mark_safe(
        f'<div style="display:flex;flex-direction:column">{inner}</div>'
    )


def _split_top_level(html_str: str) -> list[str]:
    parser = _TopLevelSplitter()
    parser.feed(html_str)
    parser.close()
    return parser.finalize()


def _inject_order_style(part: str, order: int) -> str:
    """
    Element start tag'iga `style="order:N"` qo'shadi.
    Mavjud style bo'lsa, qo'shimcha qo'shadi.
    Element bo'lmagan bo'lsa (matn), oddiy <span> ga o'raydi.
    """
    m = _TAG_START_RE.match(part.lstrip())
    if not m:
        # Top-level matn — span ga o'rab orderni qo'shamiz
        return f'<span style="order:{order}">{part}</span>'

    # Element start tag bor → style inject qilamiz
    leading_ws_len = len(part) - len(part.lstrip())
    leading = part[:leading_ws_len]
    rest = part[leading_ws_len:]

    # Mavjud style="..." bormi?
    style_re = re.compile(r'style\s*=\s*"([^"]*)"', re.IGNORECASE)
    sm = style_re.search(rest, m.end())
    if sm:
        # Existing style'ga qo'shamiz
        new_style = f'order:{order};{sm.group(1)}'
        rest = rest[:sm.start()] + f'style="{new_style}"' + rest[sm.end():]
    else:
        # Tag oxiriga qo'shamiz (>` dan oldin)
        end_char = m.group(2)
        insert_pos = m.start(2)
        rest = (
            rest[:insert_pos]
            + f' style="order:{order}"'
            + rest[insert_pos:]
        )

    return leading + rest
