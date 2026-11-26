"""Dev settings with the development requirements must serve requests (debug toolbar URLs)."""

import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]

PROBE = """
import django
django.setup()
from django.test import Client
r = Client().get('/ping/')  # no database: the toolbar middleware runs on every request
print('STATUS', r.status_code)
"""


def test_dev_settings_answer_requests():
    """The E2E job runs core.settings.dev; a toolbar without its URLs made every request 500."""
    import os

    env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'core.settings.dev'}
    out = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, '-c', PROBE],
        cwd=BASE,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert 'STATUS 200' in out.stdout, out.stdout[-500:] + out.stderr[-2000:]
