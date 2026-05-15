"""
Dynamic Watermark Service.

Hybrid render:
  1) Server-side Pillow image (PNG) — background layer
  2) Client-side CSS overlay text  — frontend layer

Cache: Redis 1 soat TTL — bir xil user/org bir necha marta so'rasa, Pillow qayta render qilmaydi.

Cache key: f"wm:img:{user_id}:{org_id}"
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
from dataclasses import dataclass

from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)


# ── Sozlamalar ─────────────────────────────────────────────────

CACHE_TTL = 60 * 60  # 1 soat

# Watermark image o'lchamlari
TILE_WIDTH = 600
TILE_HEIGHT = 400

# Matn ko'rinishi
FONT_SIZE = 22
TEXT_FILL = (128, 128, 128, 60)  # RGBA, alpha=60/255
ROTATION_DEG = -30  # diagonal


# ── Data class ─────────────────────────────────────────────────


@dataclass(frozen=True)
class WatermarkData:
    user_id: str
    full_name: str
    org_name: str

    def cache_key(self) -> str:
        h = hashlib.md5(f'{self.user_id}|{self.full_name}|{self.org_name}'.encode()).hexdigest()[
            :16
        ]
        return f'wm:img:{h}'

    def display_text(self) -> str:
        # 2 satr: ID + ism, org nomi
        return f'{self.full_name} · {self.user_id}\n{self.org_name}'


# ── Redis ──────────────────────────────────────────────────────


def _redis():
    import redis

    return redis.Redis.from_url(
        getattr(settings, 'REDIS_URL', 'redis://127.0.0.1:6379/1'),
        decode_responses=False,  # bayt-baytni saqlash
    )


# ── Asosiy API ─────────────────────────────────────────────────


def render_png(data: WatermarkData) -> bytes:
    """
    Watermark PNG bytes qaytaradi (Redis cache bilan).
    Cache miss bo'lsa, Pillow bilan render qilinadi.
    """
    cache_key = data.cache_key()
    try:
        r = _redis()
        cached = r.get(cache_key)
        if cached:
            return cached
    except Exception as e:
        log.warning('Redis cache miss (read): %s', e)

    png = _render_pillow(data)

    try:
        r = _redis()
        r.set(cache_key, png, ex=CACHE_TTL)
    except Exception as e:
        log.warning('Redis cache miss (write): %s', e)

    return png


def for_user(user, org=None) -> WatermarkData | None:
    """
    request.user va org dan WatermarkData yasash.
    Anonymous user → None qaytaradi.
    """
    if not user or not user.is_authenticated:
        return None

    full_name = (
        getattr(user, 'get_full_name', lambda: '')()
        or getattr(user, 'username', '')
        or getattr(user, 'email', '')
        or 'Anonymous'
    )
    org_name = getattr(org, 'name', '') if org else ''

    return WatermarkData(
        user_id=str(user.pk),
        full_name=str(full_name),
        org_name=str(org_name),
    )


# ── Pillow render (private) ────────────────────────────────────


def _load_font():
    """Tizimdagi truetype font yuklash, topilmasa default."""
    candidates = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        '/Library/Fonts/Arial.ttf',
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, FONT_SIZE)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_pillow(data: WatermarkData) -> bytes:
    """Diagonal joylashgan, takrorlanuvchi tile-watermark PNG yasash."""
    img = Image.new('RGBA', (TILE_WIDTH, TILE_HEIGHT), (0, 0, 0, 0))
    text = data.display_text()
    font = _load_font()

    # Aylantirish uchun alohida tile
    tile = Image.new('RGBA', (TILE_WIDTH, TILE_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)

    # Diagonal grid: ~150px qadam bilan takrorlash
    step_y = 120
    step_x = 280
    rows = math.ceil(TILE_HEIGHT / step_y) + 2
    cols = math.ceil(TILE_WIDTH / step_x) + 2

    for row in range(rows):
        for col in range(cols):
            x = col * step_x - (row % 2) * (step_x // 2)
            y = row * step_y
            draw.multiline_text(
                (x, y),
                text,
                font=font,
                fill=TEXT_FILL,
                spacing=4,
                align='left',
            )

    # Aylantirish va asosiy image bilan birlashtirish
    rotated = tile.rotate(ROTATION_DEG, resample=Image.Resampling.BICUBIC, expand=False)
    img.alpha_composite(rotated)

    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()
