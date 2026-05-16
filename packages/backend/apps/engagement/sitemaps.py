"""
Task 9 — SEO sitemap classes.

B2C savollari Google indexga ochiq. B2B (private) noindex'da qoladi
(view'da meta tag).
"""

from django.contrib.sitemaps import Sitemap

from apps.catalog.models import QuestionVersion


class PublicQuestionsSitemap(Sitemap):
    """Public questions (is_public mock'lariga tegishli yoki global)."""

    changefreq = 'weekly'
    priority = 0.7
    protocol = 'https'

    def items(self):
        # Quarantine'da bo'lmagan barcha versiyalar (latest version per question)
        return QuestionVersion.objects.filter(is_quarantined=False).order_by('-id')[:5000]

    def location(self, obj):
        return f'/questions/{obj.question_id}/'

    def lastmod(self, obj):
        return obj.created_at
