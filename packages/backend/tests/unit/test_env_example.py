"""A01 — `.env.example` lists every env key the backend reads; prod fails fast."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
CHECKER = BACKEND / 'scripts' / 'check_env_example.py'


def _load_checker():
    spec = importlib.util.spec_from_file_location('check_env_example', CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.unit
class TestEnvExample:
    def test_repo_env_example_is_complete(self):
        checker = _load_checker()
        missing, unused = checker.check(BACKEND, BACKEND / '.env.example')
        assert missing == [], f'add to packages/backend/.env.example: {missing}'
        assert unused == [], f'not read anywhere, remove from .env.example: {unused}'

    def test_new_getenv_key_is_caught(self, tmp_path):
        checker = _load_checker()
        (tmp_path / 'core').mkdir()
        (tmp_path / 'core' / 'settings.py').write_text(
            'import os\n'
            "A = os.getenv('KNOWN_KEY', '')\n"
            "B = os.environ.get('BRAND_NEW_KEY')\n"
            "C = os.environ['THIRD_KEY']\n"
        )
        (tmp_path / 'tests').mkdir()
        (tmp_path / 'tests' / 'test_x.py').write_text("import os\nos.getenv('TEST_ONLY_KEY')\n")
        example = tmp_path / '.env.example'
        example.write_text('# comment\nKNOWN_KEY=\n# OPTIONAL_COMMENTED=1\n')
        missing, _ = checker.check(tmp_path, example)
        assert missing == ['BRAND_NEW_KEY', 'THIRD_KEY']

    def test_commented_key_counts_as_documented(self, tmp_path):
        checker = _load_checker()
        (tmp_path / 'a.py').write_text("import os\nos.getenv('OPT_KEY')\n")
        example = tmp_path / '.env.example'
        example.write_text('# OPT_KEY=value\n')
        assert checker.check(tmp_path, example) == ([], [])

    def test_cli_exit_code(self, tmp_path):
        (tmp_path / 'a.py').write_text("import os\nos.getenv('MISSING_KEY')\n")
        (tmp_path / '.env.example').write_text('\n')
        proc = subprocess.run(
            [sys.executable, str(CHECKER), '--root', str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 1
        assert 'MISSING_KEY' in proc.stdout + proc.stderr


REQUIRED_PROD = {
    'SECRET_KEY': 'x' * 50,
    'DB_PASSWORD': 'pw',
    'DB_HOST': 'postgres-primary',
    'REDIS_URL': 'redis://redis:6379/1',
    'CELERY_BROKER_URL': 'redis://redis:6379/0',
    'ALLOWED_HOSTS': 'yuzdanyuz.uz',
    'TELEGRAM_WEBHOOK_SECRET': 'whsec',
    'TRUSTED_PROXY_CIDRS': '10.42.0.0/16',
}


def _import_prod(env_overrides):
    env = {k: v for k, v in os.environ.items() if k not in REQUIRED_PROD}
    env.pop('DJANGO_SETTINGS_MODULE', None)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, '-c', 'import core.settings.prod'],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.unit
class TestProdFailFast:
    def test_all_required_present_imports(self):
        proc = _import_prod(REQUIRED_PROD)
        assert proc.returncode == 0, proc.stderr

    @pytest.mark.parametrize('key', sorted(REQUIRED_PROD))
    def test_missing_required_key_fails(self, key):
        env = dict(REQUIRED_PROD)
        del env[key]
        proc = _import_prod(env)
        assert proc.returncode != 0
        assert 'ImproperlyConfigured' in proc.stderr
        assert key in proc.stderr

    def test_blank_value_counts_as_missing(self):
        proc = _import_prod({**REQUIRED_PROD, 'DB_PASSWORD': '   '})
        assert proc.returncode != 0
        assert 'DB_PASSWORD' in proc.stderr

    def test_all_missing_keys_reported_at_once(self):
        env = dict(REQUIRED_PROD)
        del env['DB_PASSWORD']
        del env['TRUSTED_PROXY_CIDRS']
        proc = _import_prod(env)
        assert 'DB_PASSWORD' in proc.stderr and 'TRUSTED_PROXY_CIDRS' in proc.stderr
