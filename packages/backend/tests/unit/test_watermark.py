"""catalog.services.watermark: the Pillow tile render and its Redis cache.

watermark.py is the only Pillow user in the backend, so these tests are what a
Pillow upgrade is checked against.
"""

import io

import fakeredis
import pytest
from django.contrib.auth.models import AnonymousUser
from PIL import Image, ImageChops

from apps.catalog.services import watermark

DATA = watermark.WatermarkData(user_id='42', full_name='Oʻgʻiloy Toʻxtayeva', org_name='Yuzdanyuz')
OTHER = watermark.WatermarkData(user_id='43', full_name='Ali Valiyev', org_name='Yuzdanyuz')


def _open(png: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(png))
    img.load()
    return img


@pytest.mark.unit
class TestRender:
    def test_png_tile_with_alpha(self):
        img = _open(watermark._render_pillow(DATA))

        assert img.format == 'PNG'
        assert img.size == (watermark.TILE_WIDTH, watermark.TILE_HEIGHT)
        assert img.mode == 'RGBA'

    def test_text_is_drawn_and_stays_faint(self):
        hist = _open(watermark._render_pillow(DATA)).getchannel('A').histogram()
        drawn = sum(hist[1:])
        strongest = max(i for i, count in enumerate(hist) if count)

        assert drawn > 1000  # the text repeats across the whole tile
        assert hist[0] > drawn  # mostly transparent between the lines
        # The bicubic rotation overshoots at stroke edges. Peak alpha for a fill of
        # 60, the same on Pillow 11.2.1 and 13.0.0: 69 with the default font (the
        # image has no TTF fonts), 80 with LiberationSans, 84 with DejaVuSans
        # (CI runner). An opaque fill would be far above this bound.
        assert strongest <= watermark.TEXT_FILL[3] * 1.5

    def test_each_user_gets_a_different_image(self):
        a = _open(watermark._render_pillow(DATA))
        b = _open(watermark._render_pillow(OTHER))

        assert ImageChops.difference(a, b).getbbox() is not None

    def test_render_is_deterministic(self):
        # The cache relies on the same input always producing the same PNG.
        assert watermark._render_pillow(DATA) == watermark._render_pillow(DATA)


@pytest.mark.unit
class TestRenderPngCache:
    def test_second_call_is_served_from_redis(self, monkeypatch):
        server = fakeredis.FakeRedis()
        monkeypatch.setattr(watermark, '_redis', lambda: server)
        renders = []
        real_render = watermark._render_pillow

        def counting_render(data):
            renders.append(data)
            return real_render(data)

        monkeypatch.setattr(watermark, '_render_pillow', counting_render)

        first = watermark.render_png(DATA)
        second = watermark.render_png(DATA)

        assert first == second
        assert renders == [DATA]
        assert server.get(DATA.cache_key()) == first
        assert 0 < server.ttl(DATA.cache_key()) <= watermark.CACHE_TTL

    def test_redis_down_still_returns_the_image(self, monkeypatch):
        def redis_down():
            raise ConnectionError('redis is down')

        warnings = []
        monkeypatch.setattr(watermark, '_redis', redis_down)
        monkeypatch.setattr(watermark.log, 'warning', lambda msg, *a: warnings.append(msg % a))

        img = _open(watermark.render_png(DATA))

        assert img.size == (watermark.TILE_WIDTH, watermark.TILE_HEIGHT)
        assert len(warnings) == 2  # read and write both failed, neither raised


@pytest.mark.unit
class TestForUser:
    def test_anonymous_gets_no_watermark(self):
        assert watermark.for_user(AnonymousUser()) is None
        assert watermark.for_user(None) is None

    def test_display_text_names_the_user_and_org(self):
        assert DATA.display_text() == 'Oʻgʻiloy Toʻxtayeva · 42\nYuzdanyuz'
        assert DATA.cache_key() != OTHER.cache_key()
