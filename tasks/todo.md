# todo.md — Tracking-Node Architecture Build

> Per CLAUDE.md Task Management. Source task: build the project memory / tracking-node
> architecture for the Autonomous Founder Identity Engine. Scaffolding/planning only — **no application code**.
> Grounding docs: [`polyglot_architecture_blueprint.md`](../polyglot_architecture_blueprint.md), [`alpha_blueprint.md`](../alpha_blueprint.md).

## Plan (checkable items)

### Phase 0 — Read & reconcile
- [x] Read `polyglot_architecture_blueprint.md` fully (topology, stack, boundaries)
- [x] Read `alpha_blueprint.md` fully (persona + immutable axioms)
- [x] Reconcile task constraints vs blueprints; flag conflicts/gaps (multi-tenancy gap found — flag, don't guess)
- [x] Extract the 4 axioms verbatim; confirm count = 4 (no inventions)

### Phase 1 — North Star
- [x] Create `project_vision.md` — objectives, polyglot constraint, 4 verbatim axioms, multi-tenancy flag

### Phase 2 — Ledgers
- [x] Create `tasks/task_ledger.md` — Sprint 1/2/3, atomic tasks (checkbox + file target + acceptance criterion); Sprint 3 carries human-in-the-loop / dry-run gate
- [x] Create `tasks/lessons.md` — Engineering Constraints | Debugging Resolutions | Architectural Learnings

### Phase 3 — Conditional nodes (only if blueprint-justified)
- [x] Evaluate + create `api_contracts.md` — JUSTIFIED by §2/§5 (edge↔worker contract fully specified)
- [x] Evaluate + create `database_schema.md` — JUSTIFIED by §3 (full DDL) + needed to document the tenant-isolation gap

### Phase 4 — Cross-link & verify
- [x] Cross-link nodes (task_ledger → vision axioms; api_contracts → architecture constraint; etc.)
- [x] Run verification on resulting file structure
- [x] Report open clarifications (axioms, multi-tenant model, Sprint 3 autonomy level)

## Review
See bottom of file — completed after build.

---

## Review (post-build)

**Files created (6):**
- `project_vision.md` — North Star + 4 verbatim axioms + polyglot/multi-tenant constraints
- `tasks/task_ledger.md` — Sprint 1 (from §4 S1–S6), Sprint 2 (cognitive routing, derived), Sprint 3 (hand on the trigger, human-gated)
- `tasks/lessons.md` — living ledger, 3 sections, seeded with this session's constraints
- `tasks/todo.md` — this file
- `api_contracts.md` — edge↔worker contract (justified)
- `database_schema.md` — schema + tenant-isolation gap (justified)

**Acceptance criteria status:**
- [x] All justified files created and internally cross-linked
- [x] Axioms traceable to `alpha_blueprint.md` (zero inventions; count = 4 confirmed)
- [x] Every Sprint 1 task atomic with file target + acceptance criterion
- [x] Sprint 3 tasks carry explicit human-in-the-loop / dry-run guardrail
- [x] CLAUDE.md referenced files exist: `tasks/todo.md`, `tasks/lessons.md`, `tasks/task_ledger.md`

**Open clarifications:** raised in final report (axioms definitions, multi-tenant model, Sprint 3 autonomy level).
