# Autonomous Founder Identity Engine (AFIE)

[![CI](https://github.com/rakibulmehedi/afie/actions/workflows/ci.yml/badge.svg)](https://github.com/rakibulmehedi/afie/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/rakibulmehedi/afie)](https://github.com/rakibulmehedi/afie/releases)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Node 20+](https://img.shields.io/badge/node-20+-green.svg)](https://nodejs.org/)

> AI-powered multi-tenant SaaS that operates founder identities in public, autonomously, at institutional-grade quality.

AFIE ingests real engineering signal (GitHub commits, Telegram notes), synthesizes persona-true content drafts via LLM, gates every draft behind **human approval**, then distributes approved content across platforms — with full saga auditability and at-least-once durability.

## Table of Contents

- [Architecture](#architecture)
- [Getting Started](#getting-started)
- [Project Structure](#project-structure)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

## Architecture

Two runtimes, **never merged**, sharing a directory tree but not a dependency graph:

| Layer | Runtime | Host | Responsibility |
|-------|---------|------|----------------|
| **Edge / API** | Next.js (TypeScript) `apps/web` | Vercel | Webhook ingress, tenant resolution, approval dashboard |
| **AI Worker** | FastAPI (Python) `apps/ai-worker` | Railway | Saga orchestration, LLM synthesis, DB writes (tenant-scoped) |

```
GitHub commits / Telegram notes
        ↓
   apps/web (Next.js)        ← webhook ingress, 202 immediately
        ↓ enqueue
   apps/ai-worker (FastAPI)  ← saga: synthesize → gate → distribute
        ↓ LLM
   Human Approval Dashboard  ← every draft reviewed before publish
        ↓ approved
   Content Platforms          ← distributed with at-least-once durability
```

**Key design invariants:**
- `apps/web` never writes to Postgres and never holds LLM keys
- `apps/ai-worker` is the sole Postgres writer; all reads are tenant-scoped via RLS (`SET LOCAL app.current_tenant`)
- Every content draft requires explicit human approval before distribution

## Getting Started

**Prerequisites:** Python 3.11+, Node.js 20+, pnpm 9+, Docker (for Postgres)

```bash
git clone https://github.com/rakibulmehedi/afie.git
cd afie
pnpm install
```

**AI Worker:**
```bash
cd apps/ai-worker
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**Web App:**
```bash
cd apps/web
pnpm dev
```

## Project Structure

```
afie/
├── apps/
│   ├── ai-worker/          # FastAPI — saga orchestration, LLM, DB
│   │   ├── domain/         # Models, port protocols
│   │   ├── infrastructure/ # DB repos, LLM adapters
│   │   └── application/    # Use-case classes
│   └── web/                # Next.js — webhooks, approval dashboard
│       ├── db/             # Database access layer
│       ├── messaging/      # Queue integration
│       └── webhooks/       # Ingress handlers
├── .github/
│   ├── workflows/ci.yml    # CI: Python lint/test + Node build
│   └── ISSUE_TEMPLATE/     # Bug reports, feature requests
├── docs/                   # Architecture blueprints, API contracts
├── CHANGELOG.md
├── CONTRIBUTING.md (in .github/)
└── LICENSE                 # MIT
```

## Development

```bash
# Python (ai-worker)
cd apps/ai-worker && pytest

# Node (web)
pnpm --filter web build
```

Commits follow `type: description` (feat/fix/chore/refactor/docs/test).

See [CONTRIBUTING.md](.github/CONTRIBUTING.md) for full setup and PR process.

## Contributing

Bug reports → [GitHub Issues](https://github.com/rakibulmehedi/afie/issues)
Questions → [GitHub Discussions](https://github.com/rakibulmehedi/afie/discussions)
Security vulnerabilities → [SECURITY.md](SECURITY.md) (email only, do not open public issues)

## License

MIT © 2026 [Rakibul Islam Mehedi](https://github.com/rakibulmehedi)
