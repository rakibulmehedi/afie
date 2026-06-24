# api_contracts.md — Edge ↔ Worker Contract

> **Why this node exists:** the blueprint (§2 topology, §1.5 SLO/retry ownership, §5.1 signature rules)
> fully specifies the boundary between the Next.js edge and the Python worker. A multi-tenant polyglot
> system lives or dies on this contract being explicit and stable — so it is **justified** as a tracking node.
> Upholds the polyglot constraint → [`project_vision.md`](project_vision.md) §2.
> Schema referenced here → [`database_schema.md`](database_schema.md). Tasks → [`tasks/task_ledger.md`](tasks/task_ledger.md) Sprint 2.

---

## 0. The load-bearing order

**verify → publish → 202.** Nothing untrusted reaches the broker, DB, or LLM. The edge performs a
**single network side effect** (publish to QStash) and **never** writes Postgres (§0.1) — this is the
whole reason the system has no dual-write hazard.

---

## 1. Inbound: Webhook → Edge (`apps/web`)

| Source | Route file | Auth mechanism | Edge action on valid | Edge action on invalid |
|---|---|---|---|---|
| **GitHub** | `src/app/api/webhooks/github/[tenantId]/route.ts` | `X-Hub-Signature-256` = `sha256=` HMAC-SHA256(`GITHUB_WEBHOOK_SECRET`, **raw body bytes**), constant-time compare | publish + **202** | **401**, do not publish |
| **Telegram** | `src/app/api/webhooks/telegram/route.ts` | `X-Telegram-Bot-Api-Secret-Token` bearer, constant-time equality (NOT HMAC) | publish + **202** | **401**, do not publish |

- **Raw bytes only:** read via `request.arrayBuffer()`; no body-reading middleware before the route;
  never HMAC re-serialized JSON (§5.1, §5.3.1–5.3.2).
- **Replay window (GitHub):** ±5-minute freshness window on `created_at`/`pushed_at`/`updated_at` in
  the payload (implemented 2026-06-24). Stale → **401**, no publish. Idempotency key ≠ replay
  protection (§5.3.3).
- **Idempotency key extraction:** GitHub `X-GitHub-Delivery` (must be well-formed **UUID**); Telegram
  `update_id` (must be **positive int > 0**). Malformed → **400**, do not coerce (§5.3.7).
- **Telegram unknown user:** valid token but no `telegram:user:{fromId}` in Redis → **200 OK**, no
  publish (intentional: stops Telegram's retry loop; the token was valid, the user is simply not yet
  registered).

**Edge response contract:** `202 Accepted` (SLO **p99 < 100 ms**, §1.5), empty/minimal body. The edge
returns 202 *before* any LLM work exists.

---

## 2. Edge → QStash (publish)

```
POST (QStash publish)  — carries:
  - idempotency_key      : <github X-GitHub-Delivery uuid | telegram update_id>
  - source               : "github" | "telegram"
  - raw_payload          : <original webhook body, allowlisted downstream>
```
- Transport auth: `QSTASH_TOKEN` (edge env).
- Edge holds **no** Postgres connection (connection-pool-free by construction, §0.1/§5.2).
- Payload size guarded downstream by DB `CHECK octet_length(raw_payload::text) < 65536`.

---

## 3. QStash → Worker consume (`apps/ai-worker`)

| Concern | Contract |
|---|---|
| Endpoint | public URL (treated as non-secret) |
| Auth | verify `Upstash-Signature` (current **+** next signing keys) **with live clock** (`nbf`/`exp` ≈5 min). Signature-only without clock is replayable (§5.1, §5.3.4) |
| Invalid | **401**, no saga mutation |
| Ack discipline | **fast 200 ack, then process async** — QStash owns *delivery* retry; the worker owns the *LLM-call* retry keyed on the saga, so a redelivery resumes rather than duplicates (§1.5) |
| Delivery semantics | **at-least-once** — every handler is idempotent (see §5) |
| Rate limiting | enforced at infra/proxy layer (URL is not secret) (§5.3.5) |

Worker env for this path: `QSTASH_CURRENT_SIGNING_KEY`, `QSTASH_NEXT_SIGNING_KEY`, `DATABASE_URL` (`worker_rw`), `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`.

---

## 4. Worker → external (LLM + distribution)

- **LLM call:** per-attempt timeout **60 s**; **3 attempts**, exp backoff ≈ 2 s → 8 s → 30 s, then **DLQ + saga `FAILED`** (§1.5). Untrusted `raw_payload` text is **user-tier** input, never system prompt (§5.3.6).
- **Distribution post:** carries `posting_token` (or a "have I already posted this draft?" pre-check) so a redelivered distribution message cannot double-post publicly (§3.1). **Synthesis is freely retryable; distribution is not.**
- **Read path:** dashboard reads saga + draft state from Postgres (system of record); writes only via the human-approval API.

---

## 5. Idempotency guarantees (the contract that makes at-least-once safe)

| Guard | Constraint | Effect |
|---|---|---|
| Ingest | `UNIQUE (source, idempotency_key)` + `ON CONFLICT DO NOTHING` | duplicate webhook → no-op |
| Saga | `UNIQUE (ingest_id)` | re-consume cannot spawn a 2nd saga |
| Draft | `UNIQUE (saga_id, target_platform)` | retry cannot duplicate a platform draft |
| Post | `posting_token` / pre-check | redelivery cannot double-post externally |

Full DDL and column semantics → [`database_schema.md`](database_schema.md).

---

## 6. SLO / retry summary (§1.5)

| Metric | Target |
|---|---|
| Edge ack (202) | p99 < 100 ms |
| Per-attempt LLM timeout | 60 s |
| Retry policy | 3 attempts, exp backoff → DLQ |
| End-to-end (webhook → `AWAITING_APPROVAL`) | p99 < 90 s at ρ ≤ 0.7 |

---

## 7. Multi-tenancy — Model C (RESOLVED, foundation-first)

This contract is **tenant-scoped**. Decision locked 2026-06-24 → Model C
([`project_vision.md`](project_vision.md) §4, [`database_schema.md`](database_schema.md) §2).

- **Edge tenant resolution (before publish):** the edge maps each inbound webhook to a `tenant_id`
  (e.g. by registered repo/owner or bot identity) and includes it in the QStash envelope. Resolution
  happens **before** verify→publish; an unresolvable tenant → reject (do not publish).
- **Worker tenant scoping (RLS):** the worker issues `SET LOCAL app.current_tenant = '<uuid>'` at the
  start of every request transaction. Fail-closed: no context → zero rows
  ([`database_schema.md`](database_schema.md) §2.1).
- **Per-tenant secret scoping:** distribution credentials (X/LinkedIn/Telegram tokens) are resolved
  per tenant; LLM keys remain worker-global (not tenant secrets).
- **Sequencing:** webhook routing (incl. tenant resolution) is **not implemented** until the DB
  isolation foundation is verified — see [`tasks/task_ledger.md`](tasks/task_ledger.md) S4 (done/verified)
  → Sprint 2.
