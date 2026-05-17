VENV   = venv/bin
PYTHON = $(VENV)/python
MANAGE = $(PYTHON) manage.py
PG_DATA = $(HOME)/.local/pgsql/data
PG_BIN  = $(HOME)/.local/pgsql/bin
PGSHAREDIR = $(HOME)/.local/pgsql/share

.PHONY: help db-start db-stop run migrate makemigrations shell test test-fast test-unit test-integration \
        test-security test-coverage test-all createsuperuser collectstatic install install-dev

help:
	@echo "Mavjud buyruqlar:"
	@echo "  make db-start         — PostgreSQL ishga tushirish"
	@echo "  make db-stop          — PostgreSQL to'xtatish"
	@echo "  make run              — dev server ishga tushirish"
	@echo "  make migrate          — migratsiyalarni qo'llash"
	@echo "  make makemigrations   — yangi migratsiya yaratish"
	@echo "  make shell            — Django shell"
	@echo "  make test             — barcha testlar (tez, -x)"
	@echo "  make test-fast        — parallel (-n auto) + reuse-db (1.8x+ speedup)"
	@echo "  make test-unit        — unit testlar qo'llash"
	@echo "  make test-integration — integration testlar qo'llash"
	@echo "  make test-security    — security testlar qo'llash"
	@echo "  make test-coverage    — coverage report bilan"
	@echo "  make test-all         — barcha testlar (verbose)"
	@echo "  make createsuperuser"
	@echo "  make collectstatic"
	@echo "  make install          — base paketlar"
	@echo "  make install-dev      — dev paketlar"

db-start:
	@rm -f $(PG_DATA)/postmaster.pid
	@PGSHAREDIR=$(PGSHAREDIR) $(PG_BIN)/postgres -D $(PG_DATA) -p 5992 &
	@echo "PostgreSQL ishga tushdi (port 5992)"

db-stop:
	@PGSHAREDIR=$(PGSHAREDIR) $(PG_BIN)/pg_ctl -D $(PG_DATA) stop || true

run:
	$(MANAGE) runserver 0.0.0.0:8000

migrate:
	$(MANAGE) migrate

makemigrations:
	$(MANAGE) makemigrations

shell:
	$(MANAGE) shell_plus 2>/dev/null || $(MANAGE) shell

test:
	$(PYTHON) -m pytest -x -q 2>/dev/null || $(MANAGE) test

test-fast:
	$(PYTHON) -m pytest -n auto

test-unit:
	$(PYTHON) -m pytest tests/unit/ -v

test-integration:
	$(PYTHON) -m pytest tests/integration/ -v

test-security:
	$(PYTHON) -m pytest tests/security/ -v

test-coverage:
	$(PYTHON) -m pytest --cov=apps --cov=core \
		--cov-report=term-missing \
		--cov-report=html:htmlcov \
		--cov-fail-under=80

test-all:
	$(PYTHON) -m pytest tests/ apps/ -v --tb=short

createsuperuser:
	$(MANAGE) createsuperuser

collectstatic:
	$(MANAGE) collectstatic --no-input

install:
	$(VENV)/pip install -r requirements/base.txt

install-dev:
	$(VENV)/pip install -r requirements/dev.txt
