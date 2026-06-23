# lessons.md — Living Ledger

> The self-improvement ledger referenced by [`../CLAUDE.md`](../CLAUDE.md) §3.
> After ANY correction from the user, append a rule here that prevents the same mistake.
> Review at session start. Entry format: `date — context — resolution`.
> Related: [`../project_vision.md`](../project_vision.md) · [`./task_ledger.md`](./task_ledger.md).

---

## Engineering Constraints

- `2026-06-24 — polyglot boundary — Next.js (apps/web) and Python (apps/ai-worker) must NEVER be merged into one runtime. They share a directory tree but not a dependency graph: apps/ai-worker is intentionally NOT a pnpm workspace member (no package.json ⇒ pnpm/turbo ignore it). uv owns Python deps, pnpm owns JS deps.`
- `2026-06-24 — edge is publish-only — the Vercel edge does exactly verify → publish to QStash → 202. It NEVER writes Postgres and gets NO DATABASE_URL. This is what removes the non-atomic dual-write hazard; do not add a "convenience" DB write on the edge.`
- `2026-06-24 — secret placement — LLM keys (ANTHROPIC_API_KEY, GEMINI_API_KEY) live ONLY on the worker (Railway). Never in apps/web, never bundled to client, never NEXT_PUBLIC_.`
- `2026-06-24 — human-in-the-loop is immutable — nothing posts without human approval. Synthesis (retryable, no external effect) is strictly separated from distribution (externally visible, posting_token-guarded). Sprint 3 inherits this; dry-run defaults ON.`

## Debugging Resolutions

- `2026-06-24 — (seed) idempotency vs replay — an idempotency key is NOT replay protection. GitHub needs a freshness/replay window in addition to the X-GitHub-Delivery UUID uniqueness (blueprint §5.3.3). Remember both when debugging duplicate/late deliveries.`
- `2026-06-24 — (seed) double-retry race — QStash owns DELIVERY retry; the worker owns the LLM-call retry keyed on the saga. Worker must fast-ack (200) then process async, or a QStash push-timeout will race a 60s LLM call and duplicate work (§1.5).`

## Architectural Learnings

- `2026-06-24 — multi-tenancy gap — the build task says "multi-tenant" but blueprint DDL §3 has NO tenant_id / RLS / isolation primitive; only persona/persona_version attribution columns exist. Treated as a GAP (persona columns are a compatible seed), not a contradiction. Did NOT invent a tenancy strategy — raised as open clarification #2. Lesson: when task and grounding doc diverge, flag + ask, don't backfill.`
- `2026-06-24 — RESOLVED clarification #2 → Model C (True Multi-Tenant SaaS) — user locked the decision. Implemented as: tenants (root) + tenant_blueprints (isolated cognitive state) + universal_axioms (shared, immutable, NOT tenant-scoped) + tenant_id on every pipeline table + RLS ENABLE/FORCE fail-closed + composite FKs for cross-tenant integrity. Migration 0001_multi_tenant_init.sql VERIFIED against a real PostgreSQL cluster (10 isolation/FK/immutability/FSM tests passed). Lesson: enforce tenant isolation in the DB (RLS fail-closed), not in app code; fail-closed = NULL tenant context returns zero rows.`
- `2026-06-24 — provisioning vs fail-closed RLS — a new tenant can't be INSERTed under fail-closed RLS (no context yet), so onboarding needs a separate BYPASSRLS role (tenant_admin) used ONLY for onboarding, never request handling. worker_rw stays subject to RLS. Lesson: fail-closed isolation creates a bootstrap problem — solve it with a narrow privileged onboarding path, not by weakening worker_rw.`
- `2026-06-24 — axiom extraction — alpha_blueprint.md names 4 immutable axioms (Engineering Rigor, Physical Contrast, Business Translation, Signal-to-Noise) in framework headers and confirms the count, but gives NO standalone definition sentence per axiom. Recorded verbatim names + sources; flagged that per-axiom definitions are inferred-not-quoted. Lesson: distinguish "named verbatim" from "defined verbatim"; never fabricate definitions to look complete.`
- `2026-06-24 — saga is the single source of truth — ingest_queue is landing-only; the worker advances feature_sagas as the one state machine, in one tx, to avoid two divergent state machines (blueprint §3.2 / H4).`
- `2026-06-24 — lazy env init for Next.js route handlers — module-level throw (!env) works at runtime but crashes turbopack static page-data collection at build time even with export const dynamic = "force-dynamic". Fix: move env guard inside the handler/helper function (lazy init). Apply to all edge routes and lib helpers.`
- `2026-06-24 — jest.mock hoisting order — jest.mock() is hoisted to before process.env.X = "..." in-test assignments, so module-level env checks fire before test env is set. Fix: use setupFiles in jest.config.js to set env before any module loads; never rely on in-test process.env for module-level guards.`
