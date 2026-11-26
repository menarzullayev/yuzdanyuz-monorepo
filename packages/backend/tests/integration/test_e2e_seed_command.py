"""The E2E seed command is development-only and idempotent for the student."""

import io
import json

import pytest
from django.core.management import CommandError, call_command


@pytest.mark.integration
class TestE2ESeed:
    def test_refuses_without_debug(self, db, settings, monkeypatch):
        settings.DEBUG = False
        monkeypatch.setenv('E2E_PASSWORD', 'a-long-enough-password')
        with pytest.raises(CommandError, match='DEBUG'):
            call_command('e2e_seed')

    def test_refuses_short_password(self, db, settings, monkeypatch):
        settings.DEBUG = True
        monkeypatch.setenv('E2E_PASSWORD', 'short')
        with pytest.raises(CommandError, match='E2E_PASSWORD'):
            call_command('e2e_seed')

    def test_seeds_student_and_two_mocks(self, db, settings, monkeypatch):
        from apps.accounts.models import CustomUser
        from apps.exams.models import MockExam
        from core.tenant import unscoped_context

        settings.DEBUG = True
        monkeypatch.setenv('E2E_PASSWORD', 'a-long-enough-password')
        out = io.StringIO()
        call_command('e2e_seed', stdout=out)
        data = json.loads(out.getvalue())
        call_command('e2e_seed', stdout=io.StringIO())  # second run: same user, new mocks

        user = CustomUser.objects.get(username='e2e.student')
        assert user.check_password('a-long-enough-password')
        with unscoped_context():
            assert MockExam.objects.filter(pk__in=data['mocks'].values()).count() == 2
            assert MockExam.objects.filter(title__startswith='E2E ').count() == 4
            short = MockExam.objects.get(pk=data['mocks']['short'])
        assert short.duration_minutes == 1 and short.question_links.count() == 4
