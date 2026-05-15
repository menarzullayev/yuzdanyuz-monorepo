# YuzDanYuz — Milliy Sertifikat

[![CI](https://github.com/menarzullayev/yuzdanyuz-monorepo/actions/workflows/ci.yml/badge.svg)](https://github.com/menarzullayev/yuzdanyuz-monorepo/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/)
[![Django](https://img.shields.io/badge/django-5.2-green.svg)](https://www.djangoproject.com/)
[![Next.js](https://img.shields.io/badge/next.js-15-black.svg)](https://nextjs.org/)
[![License](https://img.shields.io/badge/license-Proprietary-yellow.svg)]()

EdTech Super-App: DTM simulyatori + AI diagnostika + B2B white-label platforma.

## Monorepo Structure

```
yuzdanyuz-monorepo/
├── packages/
│   ├── backend/          🐍 Django 5.2 + DRF + Celery
│   ├── frontend-web/     ⚛️  Next.js 15 + React 19
│   ├── frontend-mobile/  📱 React Native + Expo
│   └── shared/           🔗 Shared TypeScript types & utilities
├── package.json          (workspace root)
└── README.md
```

## Quick Start

### Backend (Django)
```bash
cd packages/backend
source venv/bin/activate
make run
```

### Frontend (Next.js)
```bash
cd packages/frontend-web
npm install
npm run dev
```

### Full Stack
```bash
/home/hsm/scripts/run-production.sh
```

## Documentation

- **Project rules** (Claude Code): [.claude/CLAUDE.md](.claude/CLAUDE.md)
- **Lessons learned**: [.claude/LESSONS.md](.claude/LESSONS.md)
- **Backend docs**: [packages/backend/docs/](packages/backend/docs/)
- **Frontend docs**: [packages/frontend-web/docs/](packages/frontend-web/docs/)
- **Roadmap**: [packages/backend/docs/roadmap.md](packages/backend/docs/roadmap.md)
- **Deployment**: [/home/hsm/docs/deployment/](../../docs/deployment/)

## Claude Code Agent Setup

This repo includes a full Claude Code agent configuration in [.claude/](.claude/):

| Component | Path | Purpose |
|-----------|------|---------|
| **Memory** | `.claude/CLAUDE.md`, `.claude/LESSONS.md` | Project rules + self-improvement log |
| **Hooks** | `.claude/hooks/*.sh` | PostToolUse format/validate, SessionStart context, PreCompact save |
| **Skills** | `.claude/skills/*/SKILL.md` | Auto-invoked domain expertise (multitenant-rls, auth-jwt) |
| **Agents** | `.claude/agents/*.md` | Subagents (migration-validator, tenant-auditor, test-runner) |
| **Commands** | `.claude/commands/*.md` | Slash commands (`/verify`, `/ship`, `/new-task`, `/audit-tenant`, `/db-snapshot`) |
| **Statusline** | `.claude/statusline` | Bottom bar: branch, dirty count, CI status, test count |
| **Plugin** | `.claude/plugins/manifest.json` | Package metadata for redistribution |

New developers: clone the repo, hooks/skills/commands work automatically when using Claude Code.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Django 5.2, DRF, Celery, Redis |
| Database | PostgreSQL 16 (RLS enabled) |
| Frontend Web | Next.js 15, React 19, TypeScript |
| Frontend Mobile | React Native, Expo |
| Cache/Queue | Redis 7 |
| Analytics | ClickHouse (planned) |

## Status

- ✅ Task 1 — Foundation & Multi-Tenant
- ✅ Task 2 — Auth Engine + Device Fingerprinting
- ✅ Task 3 — Question Bank + Content Security
- 🔵 Task 4 — Exam Engine (next)

See [roadmap](packages/backend/docs/roadmap.md) for full details.
