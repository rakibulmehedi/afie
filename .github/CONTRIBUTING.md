# Contributing to AFIE

## Setup

**Prerequisites:** Python 3.11+, Node.js 20+, pnpm 9+, Docker

```bash
git clone https://github.com/rakibulmehedi/afie.git
cd afie
pnpm install
```

**AI Worker (Python):**
```bash
cd apps/ai-worker
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**Web (Next.js):**
```bash
cd apps/web
pnpm dev
```

## Architecture

Two runtimes, never merged:
- `apps/web` — Next.js (TypeScript), webhook ingress + approval dashboard
- `apps/ai-worker` — FastAPI (Python), saga orchestration + LLM synthesis

## Pull Request Process

1. Fork → feature branch from `main`
2. Write tests first (TDD)
3. Keep PRs focused — one concern per PR
4. Fill out the PR template completely
5. Ensure CI passes before requesting review

## Code Style

- Python: follow PEP 8, use `ruff` for linting
- TypeScript: ESLint + Prettier (config in repo root)
- Commits: `type: description` (feat/fix/chore/refactor/docs/test)

## Questions

Open a [GitHub Discussion](https://github.com/rakibulmehedi/afie/discussions) or email rakibulmehedi.dev@gmail.com
