# Changelog

All notable changes to AFIE will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Polyglot monorepo: Next.js web app (`apps/web`) + FastAPI AI worker (`apps/ai-worker`)
- Domain layer: founder persona models and port protocols (ai-worker)
- Infrastructure layer: database repositories and LLM adapters (ai-worker)
- Application layer: use-case classes (ai-worker)
- DI container wiring, API adapters to use-cases (ai-worker)
- MVVM layer separation in web app (`lib/` → `db/`, `messaging/`, `webhooks/`)
- CI/CD pipeline (GitHub Actions)
- Community health files: CONTRIBUTING, CODE_OF_CONDUCT, issue templates
- MIT license and security policy

## [0.1.0] - 2026-06-25

### Added
- Initial repository structure
- Project vision and architecture blueprints
- Polyglot architecture blueprint (Python + TypeScript, two runtimes)
- Alpha persona blueprint (`mehedi-boss-alpha` founding tenant)
- API contracts and database schema documentation
