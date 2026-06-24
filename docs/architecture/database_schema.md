# database_schema.md — PostgreSQL System of Record

> **Architecture: Model C — True Multi-Tenant SaaS** (multiple owners, isolated cognitive state,
> Universal Axioms). Decision locked 2026-06-24; supersedes the earlier single-persona design.
> **Single writer:** the Python worker (`worker_rw`) — the edge never touches Postgres
> ([`api_contracts.md`](api_contracts.md) §0). **Tenant onboarding** is the only other writer
> (`tenant_admin`, BYPASSRLS).
> **Migration:** [`apps/ai-worker/migrations/0001_multi_tenant_init.sql`](apps/ai-worker/migrations/0001_multi_tenant_init.sql)
> — **verified applied cleanly against PostgreSQL** (see §6). Task → [`tasks/task_ledger.md`](tasks/task_ledger.md) S4.

---

## 1. Tables

### Global / shared (NOT tenant-scoped)
| Table | Role | Key invariants |
|---|---|---|
| `universal_axioms` | the 4 immutable axioms, shared by **all** tenants | `id` 1..4; `name` verbatim from `alpha_blueprint.md`; **immutable** — `trg_axiom_immutable` blocks UPDATE/DELETE; **no RLS** (shared reference) |

### Tenant root
| Table | Role | Key invariants |
|---|---|---|
| `tenants` | the owner/account — root of isolation | `owner_email` UNIQUE; `status` ∈ ACTIVE/SUSPENDED/DELETED; RLS self-scoped (`id = current_tenant_id()`) |
| `tenant_blueprints` | **isolated cognitive state** per tenant (persona + config) | `tenant_id` FK; `cognitive_state` JSONB (frameworks/lexical rules/dynamic vars); `UNIQUE (tenant_id, persona, persona_version)`; `tenant_id` immutable |

### Tenant-scoped pipeline (all carry `tenant_id` + RLS)
| Table | Role | Key invariants |
|---|---|---|
| `ingest_queue` | raw landing + idempotency ledger (worker-written) | `UNIQUE (tenant_id, source, idempotency_key)`; `CHECK octet_length(raw_payload) < 65536` |
| `feature_sagas` | orchestration FSM (optimistic-locked) | `UNIQUE (ingest_id)`; `version` lock; `tenant_id` immutable; composite FK to ingest enforces same-tenant |
| `cspe_drafts` | drafts + approval + per-platform distribution | `UNIQUE (saga_id, target_platform)`; `blueprint_id` (which cognitive state produced it); `posting_token`; composite FKs to saga + blueprint |
| `feature_saga_events` | auditable transition history | records `from→to`, `actor`; composite FK to saga; `ON DELETE RESTRICT` |

### Enums
`tenant_status` · `ingest_source` · `ingest_status` · `saga_status` · `target_platform` · `approval_status` · `dist_status`.

---

## 2. Tenant Isolation (Model C — the foundation)

### 2.1 Row-Level Security (fail-closed)
Every tenant-scoped table has **`ENABLE` + `FORCE ROW LEVEL SECURITY`** with policy
`tenant_id = current_tenant_id()` (`tenants` uses `id = current_tenant_id()`).

```sql
CREATE FUNCTION current_tenant_id() RETURNS uuid LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.current_tenant', true), '')::uuid
$$;
```
- **Fail-closed:** if the GUC is unset, `current_tenant_id()` is NULL → predicate is NULL → **zero rows**
  visible or writable. Forgetting to set context **cannot** leak across tenants — it returns nothing.
- **`FORCE`** means even the table owner obeys the policy. **`worker_rw` has NO `BYPASSRLS`.**
- **Runtime contract (worker, per request tx):**
  ```sql
  BEGIN;
  SET LOCAL app.current_tenant = '<resolved-tenant-uuid>';
  ...   -- all reads/writes are auto-scoped to that tenant
  COMMIT;
  ```

### 2.2 Cross-tenant referential integrity (structural)
Child rows carry `tenant_id` **and** a **composite FK** to the parent's `(id, tenant_id)`:
- `feature_sagas (ingest_id, tenant_id) → ingest_queue (id, tenant_id)`
- `cspe_drafts (saga_id, tenant_id) → feature_sagas (id, tenant_id)`
- `cspe_drafts (blueprint_id, tenant_id) → tenant_blueprints (id, tenant_id)`
- `feature_saga_events (saga_id, tenant_id) → feature_sagas (id, tenant_id)`

A child can **never** reference a parent in another tenant — it's a foreign-key violation, not a policy
nicety. `tenant_id` is also **immutable** on `feature_sagas`/`tenant_blueprints` (rewrite trigger raises).

### 2.3 Roles (least privilege)
| Role | Purpose | RLS |
|---|---|---|
| `worker_rw` | the only runtime writer | **subject to RLS** (no BYPASSRLS) |
| `edge_ingest` | documented, **unused** (edge writes nothing) | — |
| `tenant_admin` | tenant onboarding only (create `tenants`/`tenant_blueprints`) | **BYPASSRLS** (onboarding can't satisfy fail-closed RLS); never handles requests |

---

## 3. The saga FSM (DB-enforced)

`trg_saga_fsm` raises on illegal transitions; `version` optimistic lock makes concurrent workers safe
(`UPDATE … WHERE id=$1 AND version=$expected`; `rowcount=0` ⇒ another worker won → reload/skip).

```
RECEIVED → SYNTHESIZING → DRAFTED → AWAITING_APPROVAL
AWAITING_APPROVAL → APPROVED | REJECTED | EXPIRED
APPROVED → DISTRIBUTING
DISTRIBUTING → PARTIALLY_DISTRIBUTED | DISTRIBUTED | FAILED
PARTIALLY_DISTRIBUTED → DISTRIBUTED | FAILED
SYNTHESIZING → FAILED
recovery edges: FAILED → SYNTHESIZING | DISTRIBUTING | REJECTED
```
- **Persist before side effect:** write `SYNTHESIZING`, *then* call the LLM (crash resumes correctly).
- **Synthesis vs distribution:** synthesis freely retryable (no external effect); distribution guarded by
  `posting_token` — Sprint 2 vs Sprint 3 in the ledger.

---

## 4. Idempotency constraints (at-least-once safety)

Mirrors [`api_contracts.md`](api_contracts.md) §5:
1. `ingest_queue UNIQUE (tenant_id, source, idempotency_key)` — duplicate webhook → no-op (now **per-tenant**).
2. `feature_sagas UNIQUE (ingest_id)` — one saga per ingest.
3. `cspe_drafts UNIQUE (saga_id, target_platform)` — no duplicate platform drafts.
4. `cspe_drafts.posting_token` — no public double-post on redelivery.

---

## 5. Universal Axioms (shared, immutable)

The 4 axioms are **universal across all tenants** — they live in `universal_axioms` (no `tenant_id`,
no RLS) and are protected by `trg_axiom_immutable` (UPDATE/DELETE raise). Tenants vary their *persona*
and *cognitive_state* (`tenant_blueprints`); they do **not** get private axioms. Names are verbatim from
`alpha_blueprint.md` → [`project_vision.md`](project_vision.md) §3.

| id | name | source_ref |
|----|------|-----------|
| 1 | Engineering Rigor | alpha_blueprint.md l.22,l.32 |
| 2 | Physical Contrast | alpha_blueprint.md l.32 |
| 3 | Business Translation | alpha_blueprint.md l.22,l.41 |
| 4 | Signal-to-Noise | alpha_blueprint.md l.41 |

---

## 6. Verification (proven, not asserted)

Migration applied to a throwaway PostgreSQL cluster and tested 2026-06-24:

| # | Test | Result |
|---|---|---|
| — | `0001_multi_tenant_init.sql` applies in one tx | ✅ clean |
| — | 7 tables created; 4 axioms seeded | ✅ |
| — | RLS `ENABLE`+`FORCE` on 6 tenant tables; `universal_axioms` excluded | ✅ |
| 1 | Fail-closed: no `app.current_tenant` → 0 rows | ✅ |
| 2 | Tenant B context cannot see Tenant A rows | ✅ 0 |
| 3 | Tenant A context sees its own rows | ✅ 1 |
| 4 | RLS `WITH CHECK` blocks writing a foreign `tenant_id` | ✅ ERROR |
| 5 | Cross-tenant composite FK rejected | ✅ ERROR |
| 6/7 | `universal_axioms` UPDATE/DELETE blocked | ✅ ERROR |
| 8 | `feature_sagas.tenant_id` immutable | ✅ ERROR |
| 9 | Illegal FSM transition rejected | ✅ ERROR |
| 10 | Legal transition bumps `version` | ✅ 1 |

---

## 7. Operational notes
- **Retention (blueprint §7):** `ingest_queue` — `DELETE WHERE status='PROCESSED' AND processed_at < now() - interval '90 days'` (now scope per-tenant). Audit tables retain; FKs `ON DELETE RESTRICT`.
- **PK note:** prefer **UUIDv7** for `feature_sagas`/`cspe_drafts`/`tenant_blueprints` ids at scale (blueprint M4).
- **Indexes** lead with `tenant_id` for per-tenant locality.

---

## Cross-references
- North Star + Universal Axioms + Model C → [`project_vision.md`](project_vision.md) §2, §3, §4
- Edge↔worker contract (tenant resolution) → [`api_contracts.md`](api_contracts.md) §7
- Migration & schema tasks → [`tasks/task_ledger.md`](tasks/task_ledger.md) S4
- Decision record → [`tasks/lessons.md`](tasks/lessons.md) Architectural Learnings
