"""
Security template tags — watermark va DOM obfuscation uchun.

Foydalanish:
  {% load security_tags %}
  {% watermark_overlay %}
  <div class="question-text">{% shuffle_dom %}{{ question.text }}{% endshuffle_dom %}</div>
"""

from django import template
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils.html import escape

from apps.catalog.services import watermark as wm

register = template.Library()


# ── Watermark ──────────────────────────────────────────────────

@register.simple_tag(takes_context=True)
def watermark_overlay(context):
    """
    Hybrid watermark:
      1) PNG image (server'dan, /catalog/wm.png)
      2) CSS overlay text (frontend'da)

    Admin/superuser uchun bo'sh string qaytaradi (bypass).
    """
    request = context.get('request')
    if request is None:
        return ''

    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return ''
    if user.is_superuser or user.is_staff:
        return ''

    org = getattr(request, 'org', None)
    data = wm.for_user(user, org)
    if data is None:
        return ''

    img_url = reverse('catalog:watermark_png')
    text = escape(data.display_text().replace('\n', ' • '))

    html = f"""
<div class="wm-overlay" aria-hidden="true" style="
    position: fixed; inset: 0; z-index: 9998;
    pointer-events: none; user-select: none;
    background-image: url('{img_url}');
    background-repeat: repeat;
    opacity: 0.5;
"></div>
<div class="wm-text" aria-hidden="true" style="
    position: fixed; inset: 0; z-index: 9999;
    pointer-events: none; user-select: none;
    display: flex; align-items: center; justify-content: center;
    transform: rotate(-30deg);
    color: rgba(128,128,128,0.18);
    font-size: 14px; font-weight: 600;
    text-align: center; white-space: nowrap;
">{text}</div>
""".strip()
    return mark_safe(html)


# ── DOM Shuffle ────────────────────────────────────────────────

@register.tag(name='shuffle_dom')
def shuffle_dom(parser, token):
    """
    Block ichidagi <li>, <p>, <span> kabi top-level elementlarni
    DOM darajasida tartibsiz, lekin CSS `order` orqali to'g'ri ko'rsatadi.

    Usage:
      {% shuffle_dom %}
        <p>1-band</p>
        <p>2-band</p>
        <p>3-band</p>
      {% endshuffle_dom %}
    """
    nodelist = parser.parse(('endshuffle_dom',))
    parser.delete_first_token()
    return ShuffleDomNode(nodelist)


class ShuffleDomNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        from apps.catalog.services.dom_shuffle import shuffle_html
        request = context.get('request')
        user = getattr(request, 'user', None) if request else None
        # Admin bypass
        if user and (user.is_superuser or user.is_staff):
            return self.nodelist.render(context)
        return shuffle_html(self.nodelist.render(context))
