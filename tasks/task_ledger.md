# task_ledger.md — Atomic Sprint Checklist (source of truth)

> Every micro-task: `[ ]` checkbox + explicit **file target(s)** + **testable acceptance criterion**.
> Grounding: [`../polyglot_architecture_blueprint.md`](../polyglot_architecture_blueprint.md).
> North Star + axioms: [`../project_vision.md`](../project_vision.md).
> Contracts: [`../api_contracts.md`](../api_contracts.md) · Schema: [`../database_schema.md`](../database_schema.md).
>
> **Scope discipline:** Sprint 1 is scaffold-only (no service logic). Sprints 2–3 are scoped from the
> blueprint; gaps are flagged inline rather than invented.

---

## Sprint 1 — Scaffolding

> Verbatim source: blueprint §4 (steps S1–S6). Goal: Turborepo where JS is a pnpm/Turbo workspace and
> Python lives **outside** the JS build graph. Stop at scaffold + healthcheck stubs.
> Upholds polyglot constraint → [`../project_vision.md`](../project_vision.md) §2.

- [ ] **S1 — Scaffold Turborepo + `apps/web`**
  - Files: repo root (`turbo.json`, `pnpm-workspace.yaml`), `apps/web/` (Next.js: `--typescript --app --eslint --tailwind --src-dir --import-alias "@/*"`)
  - Accept: `turbo run build` is green on the empty `apps/web`.
- [ ] **S2 — Scaffold `apps/ai-worker` with `uv` + FastAPI `/healthz` stub** *(parallel with S1)*
  - Files: `apps/ai-worker/pyproject.toml`, `apps/ai-worker/uv.lock`, `apps/ai-worker/app/main.py`
  - Deps: `fastapi uvicorn[standard] httpx pydantic-settings psycopg[binary,pool] qstash anthropic google-genai`; dev: `pytest ruff mypy`
  - Accept: `uv run uvicorn app.main:app` serves HTTP 200 on `/healthz`.
- [ ] **S3 — Declare workspace membership + ignore env/build artifacts** *(depends S1, S2)*
  - Files: `pnpm-workspace.yaml` (lists `apps/web`, `packages/*`; **NOT** `apps/ai-worker`), `.gitignore`, `apps/web/.env.example`, `apps/ai-worker/.env.example`
  - Accept: no secret committed; `.venv/`, `node_modules/`, `.turbo/`, `.env` ignored; `apps/ai-worker` absent from JS build graph (`turbo run build` does not touch Python).
- [x] **S4 — Provision Postgres + apply multi-tenant DDL `0001_multi_tenant_init.sql`** *(depends S3, parallel with S5)* — **DDL DONE + VERIFIED** (DB provisioning on real infra still pending)
  - Files: [`../apps/ai-worker/migrations/0001_multi_tenant_init.sql`](../apps/ai-worker/migrations/0001_multi_tenant_init.sql) (Model C — mirrored in [`../database_schema.md`](../database_schema.md)); roles `worker_rw` (no BYPASSRLS), `edge_ingest` (unused), `tenant_admin` (BYPASSRLS, onboarding only)
  - Accept: all enums/tables/indexes/triggers exist; **RLS `ENABLE`+`FORCE` on all 6 tenant tables**; `universal_axioms` shared+immutable with 4 seeded rows; fail-closed isolation + cross-tenant FK rejection proven ([`../database_schema.md`](../database_schema.md) §6). ✅ verified against PostgreSQL 2026-06-24.
- [ ] **S5 — Create QStash topic + DLQ + scheduled sweep; store signing keys as worker env** *(depends S3, parallel with S4)*
  - Files: infra config / `apps/ai-worker/.env.example` (key names only)
  - Accept: a publish→push round-trip reaches the `/healthz` echo stub.
- [ ] **S6 — Wire Vercel (root `apps/web`) + Railway (root `apps/ai-worker`); set env per blueprint §5** *(depends S4, S5)*
  - Files: `vercel.json` / project settings, Railway service config
  - Accept: both deploy; edge returns a **202** stub; worker boots. **STOP here — webhook/synthesis/distribution logic is Sprint 2+.**

---

## Sprint 2 — Cognitive Routing

> Derived from blueprint §1.5, §2, §3.1–3.2, §5.1–5.3. "Cognitive routing" = the saga state machine
> driving LLM synthesis (Claude/Gemini) under the **tenant's** persona blueprint. All §5.3 items are the
> enforcement contract. Synthesis is the freely-retryable half (no external effect).
> Contract: [`../api_contracts.md`](../api_contracts.md).
>
> **Model C tenancy (applies to every task below):** the edge resolves `tenant_id` before publishing;
> the worker issues `SET LOCAL app.current_tenant = '<uuid>'` at the start of each request tx so all
> reads/writes are RLS-scoped ([`../database_schema.md`](../database_schema.md) §2.1). **Blocked until
> S4 foundation is in place — webhook routing is not implemented before isolation is verified.**

- [x] **S2.1 — Edge webhook verification (GitHub HMAC + Telegram bearer)** ✅ 2026-06-24
  - Files: `apps/web/src/app/api/webhooks/github/[tenantId]/route.ts`, `apps/web/src/app/api/webhooks/telegram/route.ts`
  - Tests: `src/app/api/webhooks/__tests__/github.test.ts` (6 cases), `.../__tests__/telegram.test.ts` (7 cases) — **14/14 pass**
  - Accept: GitHub `sha256=` HMAC over **raw bytes** + `timingSafeEqual`; Telegram `timingSafeEqual` token (NOT HMAC); ±5-min freshness window; tenant resolution via Redis; unsigned/bad-sig/stale → **401, no publish**; valid → publish + **202**. ✅ verified.
- [x] **S2.2 — Edge → QStash publish with idempotency key** *(depends S2.1)* ✅ 2026-06-24
  - Files: `apps/web/src/lib/qstash.ts`, `apps/web/src/lib/redis.ts`
  - Accept: idempotency key = `X-GitHub-Delivery` (validated UUID) / `update_id` (positive int > 0); malformed → **400** (§5.3.7); **no Postgres write on edge** (§0.1); `deduplicationId` wired. ✅ `next build` green (both routes `ƒ Dynamic`).
- [x] **S2.3 — Worker consume endpoint: verify QStash signature + clock, fast-ack** ✅ 2026-06-24
  - Files: `apps/ai-worker/app/api/consume.py`, `apps/ai-worker/app/api/deps.py`, `apps/ai-worker/app/api/schemas.py`, `apps/ai-worker/app/core/settings.py`
  - Tests: `apps/ai-worker/tests/test_consume.py` — 14/14 unit tests pass
  - Accept: verifies `Upstash-Signature` (current+next keys) **with live clock** (`nbf`/`exp`); unverified → **401**; returns **200 fast**, processes async (§1.5, §5.1). ✅ verified.
- [x] **S2.4 — Idempotent ingest (ingest_queue only; saga creation is S2.5)** ✅ 2026-06-24
  - Files: `apps/ai-worker/app/db/ingest.py`, `apps/ai-worker/app/db/session.py`
  - Tests: `apps/ai-worker/tests/test_consume.py` — integration tests marked `@pytest.mark.integration` (require live DB)
  - Accept: `SET LOCAL app.current_tenant` first; `INSERT … ON CONFLICT (tenant_id, source, idempotency_key) DO NOTHING`; duplicate delivery → no-op (§3.1). ✅ implementation verified against schema.
- [x] **S2.5 — Saga FSM advance with optimistic lock + event log** ✅ 2026-06-24
  - Files: `apps/ai-worker/app/db/saga.py`, `apps/ai-worker/app/fsm.py`
  - Accept: every transition `UPDATE … WHERE id=$1 AND version=$v`; `rowcount=0` → reload/skip; status persisted **before** side effect; each transition logged to `feature_saga_events` (§3.2). Illegal transition raises (DB trigger).
- [x] **S2.6 — LLM synthesis under persona + framework selection** ✅ 2026-06-24
  - Files: `apps/ai-worker/app/synthesis/router.py`, `.../persona.py`, `.../frameworks.py`
  - Accept: produces a `cspe_drafts` row with `persona='mehedi-boss-alpha'` + explicit `persona_version`; output obeys the lexical laws ([`../project_vision.md`](../project_vision.md) §3); per-attempt timeout 60s, 3 retries → DLQ + saga `FAILED` (§1.5). **GAP — flag:** Claude-vs-Gemini routing policy is unspecified in the blueprint (see clarification at end).
  - **Decision (2026-06-24):** Defaulting to `claude-haiku-4-5-20251001` via `SYNTHESIS_MODEL` env var. Policy remains overridable; Claude-vs-Gemini routing policy formally unresolved per blueprint GAP.
- [ ] **S2.7 — Saga reaches `AWAITING_APPROVAL` with `deadline_at` set**
  - Files: `apps/ai-worker/app/fsm.py`
  - Accept: on successful synthesis, saga → `AWAITING_APPROVAL`, `deadline_at` populated; end-to-end p99 < 90s at ρ≤0.7 (§1.5).
- [ ] **S2.8 — §5.3 security enforcement (synthesis half)**
  - Files: `apps/ai-worker/app/security/payload_guard.py`
  - Accept: SSRF/prompt-injection guard on `raw_payload` (allowlisted fields only; never `fetch()` payload URLs; commit/message text treated as untrusted user-tier LLM input); `last_error` sanitized (code + short msg, no tracebacks); worker endpoint rate-limited at infra layer (§5.3.5–5.3.9).

---

## Sprint 3 — Hand on the Trigger (autonomous action layer)

> Derived from blueprint §2 (distribution fan-out), §3 (`dist_status`, `posting_token`, `EXPIRED` sweep),
> §3.1 (distribution is the **non-idempotent, externally-visible** half), §5.3.10 (DLQ alerting).
> **IMMUTABLE INVARIANT:** nothing posts without human approval ([`../project_vision.md`](../project_vision.md) §5).
> **Every task below carries an explicit human-in-the-loop / dry-run gate.**

- [ ] **S3.1 — Approval dashboard (human-in-the-loop GATE)**
  - Files: `apps/web/src/app/dashboard/**`, `apps/web/src/app/api/approve/route.ts`
  - **HITL gate:** owner-only session auth on all `/dashboard/*` routes (§5.3.8); approve/edit/reject is a **manual human action**; `AWAITING_APPROVAL → APPROVED` cannot occur without it.
  - Accept: unauthenticated request → redirect/401; human decision writes `approval_status` + `decided_at` and records actor in `feature_saga_events`; no transition is auto-fired.
- [ ] **S3.2 — `APPROVED → DISTRIBUTING` publish to Distribution Queue**
  - Files: `apps/ai-worker/app/distribution/dispatch.py`
  - **Dry-run gate:** a `DISTRIBUTION_DRY_RUN` flag (default **ON**) logs the intended post + target instead of calling any external API.
  - Accept: with dry-run ON, no external post occurs and a dry-run record is produced; only an explicit operator flip enables live posting; saga moves `APPROVED → DISTRIBUTING` only after a human-approved draft exists.
- [ ] **S3.3 — Distribution worker: idempotent external post per draft/platform**
  - Files: `apps/ai-worker/app/distribution/worker.py`
  - **Dry-run gate:** honors `DISTRIBUTION_DRY_RUN`; live path requires the flag explicitly off.
  - Accept: external post carries `posting_token` (or "already posted?" pre-check) so QStash redelivery cannot double-post (§3.1); success → `dist_status=POSTED`, `posted_at` set; per-platform via `UNIQUE (saga_id, target_platform)`.
- [ ] **S3.4 — Multi-platform fan-out state modeling**
  - Files: `apps/ai-worker/app/fsm.py`
  - **Dry-run gate:** operates only on fan-out initiated by S3.2/S3.3, so it inherits `DISTRIBUTION_DRY_RUN`; under dry-run it transitions state against dry-run records only, never live posts.
  - Accept: `DISTRIBUTING → PARTIALLY_DISTRIBUTED → DISTRIBUTED`; some-fail/some-succeed handled; failed platform retries without re-posting succeeded platforms.
- [ ] **S3.5 — `EXPIRED` sweep (prevents indefinite parking)**
  - Files: `apps/ai-worker/app/sweeps/expire.py` (QStash cron-triggered)
  - **HITL-safe:** only expires drafts the human never acted on; never auto-approves.
  - Accept: `AWAITING_APPROVAL` past `deadline_at` → `EXPIRED`; approved drafts are never expired.
- [ ] **S3.6 — DLQ alerting + recovery edges**
  - Files: `apps/ai-worker/app/alerting/dlq.py`, dashboard surface
  - **HITL gate:** recovery edges (`FAILED → SYNTHESIZING/DISTRIBUTING/REJECTED`) are **human-driven** re-drive/abandon actions, not automatic.
  - Accept: `DEAD_LETTER` raises an alert + surfaces in dashboard (§5.3.10); a human can re-drive or abandon a DLQ'd saga; no automatic re-drive fires.

---

## Cross-references
- North Star & axioms → [`../project_vision.md`](../project_vision.md)
- Edge↔worker contract → [`../api_contracts.md`](../api_contracts.md)
- Schema & tenant-isolation gap → [`../database_schema.md`](../database_schema.md)
- Lessons (corrections ledger) → [`./lessons.md`](./lessons.md)
- Working plan → [`./todo.md`](./todo.md)
