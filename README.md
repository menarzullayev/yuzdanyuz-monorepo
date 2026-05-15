# YuzDanYuz — Milliy Sertifikat

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

- **Backend**: [packages/backend/CLAUDE.md](packages/backend/CLAUDE.md) · [packages/backend/docs/](packages/backend/docs/)
- **Frontend**: [packages/frontend-web/docs/](packages/frontend-web/docs/)
- **Roadmap**: [packages/backend/docs/roadmap.md](packages/backend/docs/roadmap.md)
- **Deployment**: [/home/hsm/docs/deployment/](../../docs/deployment/)

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
