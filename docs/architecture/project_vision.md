# project_vision.md — North Star

> The single source of intent for the **Autonomous Founder Identity Engine**.
> All sprints and tracking nodes resolve back to this file.
> Grounding: [`polyglot_architecture_blueprint.md`](polyglot_architecture_blueprint.md) (system),
> [`alpha_blueprint.md`](alpha_blueprint.md) (persona).

---

## 1. Core Objective

Operate **founder identities** in public, autonomously, at institutional-grade quality — as a
**multi-tenant SaaS** (Model C). Each tenant (owner) has **isolated cognitive state** (its own persona
blueprint) while all tenants share the **Universal Axioms** (§3). The engine ingests real engineering
signal (GitHub commits, Telegram notes), synthesizes persona-true content drafts via LLM, gates every
draft behind **human approval**, then distributes approved content across platforms — with full saga
auditability and at-least-once durability.

The founding tenant's persona is **`mehedi-boss-alpha`**; it is now one tenant blueprint among many — see §3, §4.

---

## 2. Polyglot Architecture Constraint (NON-NEGOTIABLE)

Two runtimes, **never merged**, sharing a directory tree but not a dependency graph:

| Layer | Runtime | Host | Responsibility |
|---|---|---|---|
| **Edge / API** | Next.js (TypeScript), `apps/web` | Vercel | Webhook ingress (**resolve tenant** → verify → publish → **202**), per-tenant approval dashboard. **No Postgres write. No LLM keys.** |
| **AI Worker** | Python (FastAPI), `apps/ai-worker` | Railway | Saga orchestration, LLM synthesis, DB writes, distribution — **tenant-scoped via RLS** (`SET LOCAL app.current_tenant`). **Sole runtime writer of Postgres.** |

- **Broker:** Upstash QStash is the *sole durable trigger* (retry/backoff/DLQ over HTTP).
- **System of record:** PostgreSQL — written **only** by the worker.
- **The edge is publish-only** (resolves the dual-write hazard; see blueprint §0.1).
- Coexistence: `apps/ai-worker` is intentionally **not** a pnpm workspace member; `turbo` never
  builds Python, `uv` never touches `node_modules` (blueprint §4.2).

> Enforcement detail for the edge↔worker boundary lives in [`api_contracts.md`](api_contracts.md).
> Schema and tenant-isolation status live in [`database_schema.md`](database_schema.md).

---

## 3. The Universal Axioms (shared across ALL tenants)

Synthesized from the structural growth phase of 8 operators; **their voices are discarded, only
the mechanisms survive**, then filtered through **4 immutable axioms**
(`alpha_blueprint.md` line 3). The shared mechanism is a 4-state pacing spine:
`micro-friction → mechanical deconstruction → abstraction → portable axiom`.

**Multi-tenant rule (Model C):** the 4 axioms are **Universal** — they apply to every tenant and are
stored once in the shared, **immutable**, non-tenant-scoped `universal_axioms` table
([`database_schema.md`](database_schema.md) §5). Tenants vary their **persona** and **cognitive state**
(`tenant_blueprints`), **not** their axioms. No tenant gets private axioms.

### The 4 axioms (extracted VERBATIM — zero inventions, count confirmed = 4)

| # | Axiom (verbatim name) | Verbatim source | Role |
|---|---|---|---|
| **1** | **Engineering Rigor** | "Axioms 1 + 3 (Engineering Rigor → Business Translation)" (l.22); "Axioms 1 + 2 (Engineering Rigor × Physical Contrast)" (l.32) | No claim ships without a number/artifact |
| **2** | **Physical Contrast** | "Axioms 1 + 2 (Engineering Rigor × Physical Contrast)" (l.32) | **The signature axiom — none of the 8 sources have it; makes the persona non-derivative.** Fast machine vs slow terrain. |
| **3** | **Business Translation** | "Axioms 1 + 3 (Engineering Rigor → Business Translation)" (l.22); "Axioms 3 + 4 (Business Translation × Signal-to-Noise)" (l.41) | Translate the fix directly into money |
| **4** | **Signal-to-Noise** | "Axioms 3 + 4 (Business Translation × Signal-to-Noise)" (l.41) | Filter, don't flatter — polarize on purpose |

> **Traceability note (honest disclosure):** `alpha_blueprint.md` *names* all 4 axioms verbatim in
> its framework headers and confirms the count ("4 immutable axioms"), but it does **not** contain a
> standalone one-sentence definition for each. The "Role" column above is summarized from the
> frameworks/operating notes that use each axiom — it is **not** a verbatim quote and is **not** an
> invented axiom. See open clarification #1.

### The 3 narrative frameworks (each = the 4-state spine bent through an axiom pair)
1. **Telemetry → Doctrine** (Axioms 1 + 3)
2. **Fast Machine, Slow Terrain** (Axioms 1 + 2) — signature
3. **The Asymmetric Bet, Open-Sourced** (Axioms 3 + 4)

### Lexical/operating laws (immutable for synthesis)
- No claim without an artifact (number / screenshot / diff / terminal output).
- Cut every hedge ("I think / maybe / kind of / probably" → delete).
- White space is a weapon — one idea per line.
- Math beats adjectives — route subjective claims through an inequality.
- One pillar → many derivatives; never generate from a cold start.

> These laws are the synthesis contract for Sprint 2 (Cognitive Routing). See
> [`tasks/task_ledger.md`](tasks/task_ledger.md) Sprint 2.

---

## 4. Multi-Tenancy — RESOLVED: Model C (True Multi-Tenant SaaS)

**Decision locked 2026-06-24.** The engine is multi-tenant SaaS: **multiple owners**, **isolated
cognitive state per tenant**, and **Universal Axioms** shared by all. This supersedes the earlier
single-persona blueprint design (which was flagged as open clarification #2 — now closed).

Foundation (see [`database_schema.md`](database_schema.md) §1–§2, migration
[`apps/ai-worker/migrations/0001_multi_tenant_init.sql`](apps/ai-worker/migrations/0001_multi_tenant_init.sql)):
- **`tenants`** — root of isolation (the owners).
- **`tenant_blueprints`** — each tenant's isolated cognitive state (persona + `cognitive_state` config).
- **`universal_axioms`** — the 4 shared, immutable axioms (no `tenant_id`).
- **`tenant_id` on every pipeline table + RLS (`ENABLE`+`FORCE`, fail-closed)** — isolation is enforced
  by the database, not by application discipline. Cross-tenant references are blocked by composite FKs.

**Status:** DDL generated and **verified against PostgreSQL** (isolation, FK integrity, immutability,
FSM all proven — [`database_schema.md`](database_schema.md) §6). **Webhook routing is intentionally NOT
implemented** until this foundation is in place (per directive: foundation-first).

---

## 5. Human-in-the-Loop Invariant (immutable)

**Nothing posts without human approval.** `AWAITING_APPROVAL → APPROVED` is a human action on the
dashboard; synthesis (freely retryable, no external effect) is strictly separated from distribution
(externally visible, idempotency-token guarded). Sprint 3 ("Hand on the Trigger") inherits this
invariant — see [`tasks/task_ledger.md`](tasks/task_ledger.md) Sprint 3 and open clarification #3
(autonomy level).
