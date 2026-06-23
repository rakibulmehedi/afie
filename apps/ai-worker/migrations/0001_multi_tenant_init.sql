-- 0001_multi_tenant_init.sql
-- Autonomous Founder Identity Engine — Sprint 1 foundation.
-- Architecture: Model C — True Multi-Tenant SaaS (multiple owners, isolated cognitive
-- state, Universal Axioms). See database_schema.md and project_vision.md.
--
-- Invariants:
--   * Single writer at runtime: worker_rw (Python worker). The edge writes NOTHING.
--   * Tenant isolation is fail-closed via RLS: no app.current_tenant => zero rows.
--   * worker_rw has NO BYPASSRLS. Onboarding uses tenant_admin (BYPASSRLS) only.
--   * universal_axioms is shared reference data: NOT tenant-scoped, immutable.

BEGIN;

-- ============================================================================
-- EXTENSIONS
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

-- ============================================================================
-- ENUMS
-- ============================================================================
CREATE TYPE tenant_status   AS ENUM ('ACTIVE', 'SUSPENDED', 'DELETED');
CREATE TYPE ingest_source   AS ENUM ('github', 'telegram');
CREATE TYPE ingest_status   AS ENUM ('PENDING', 'CLAIMED', 'PROCESSED', 'FAILED', 'DEAD_LETTER');
CREATE TYPE saga_status     AS ENUM (
    'RECEIVED', 'SYNTHESIZING', 'DRAFTED', 'AWAITING_APPROVAL',
    'APPROVED', 'REJECTED', 'EXPIRED',
    'DISTRIBUTING', 'PARTIALLY_DISTRIBUTED', 'DISTRIBUTED', 'FAILED'
);
CREATE TYPE target_platform AS ENUM ('x', 'linkedin', 'telegram');
CREATE TYPE approval_status AS ENUM ('PENDING', 'APPROVED', 'REJECTED', 'EDITED');
CREATE TYPE dist_status     AS ENUM ('PENDING', 'POSTING', 'POSTED', 'FAILED');

-- ============================================================================
-- TENANT CONTEXT HELPER (fail-closed)
--   Reads the per-transaction GUC `app.current_tenant`. If unset/empty -> NULL,
--   which makes every RLS predicate match NO rows. The worker MUST issue
--   `SET LOCAL app.current_tenant = '<uuid>'` at the start of each request tx.
-- ============================================================================
CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.current_tenant', true), '')::uuid
$$;

-- ============================================================================
-- SHARED updated_at / tenant-immutability trigger fn
--   tenant_id is immutable on tenant-scoped tables (guarded by table name so the
--   `tenants` table, which has no tenant_id column, is never referenced wrongly).
-- ============================================================================
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  IF TG_TABLE_NAME = 'tenant_blueprints'
     AND NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
    RAISE EXCEPTION 'tenant_id is immutable on %', TG_TABLE_NAME;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 0. TENANTS  (root of isolation — the "multiple owners")
-- ============================================================================
CREATE TABLE tenants (
    id           UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_email  TEXT          NOT NULL UNIQUE,
    display_name TEXT          NOT NULL,
    status       tenant_status NOT NULL DEFAULT 'ACTIVE',
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_tenants_touch BEFORE UPDATE ON tenants
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 0a. UNIVERSAL_AXIOMS  (shared, immutable; NOT tenant-scoped)
--   The 4 immutable axioms apply to ALL tenants. Provenance: alpha_blueprint.md.
-- ============================================================================
CREATE TABLE universal_axioms (
    id         SMALLINT PRIMARY KEY,
    name       TEXT     NOT NULL UNIQUE,
    role       TEXT     NOT NULL,
    source_ref TEXT     NOT NULL,
    CONSTRAINT ck_axiom_id_range CHECK (id BETWEEN 1 AND 4)
);

-- Immutability guard: axioms may be seeded once but never altered or removed.
CREATE OR REPLACE FUNCTION block_axiom_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'universal_axioms are immutable: % blocked', TG_OP;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_axiom_immutable
    BEFORE UPDATE OR DELETE ON universal_axioms
    FOR EACH ROW EXECUTE FUNCTION block_axiom_mutation();

-- Seed: verbatim axiom names from alpha_blueprint.md framework headers.
-- (role text is summarized-not-quoted; names + source_ref are verbatim/traceable.)
INSERT INTO universal_axioms (id, name, role, source_ref) VALUES
  (1, 'Engineering Rigor',    'No claim ships without a number/artifact',       'alpha_blueprint.md l.22,l.32'),
  (2, 'Physical Contrast',    'Signature axiom; fast machine vs slow terrain',  'alpha_blueprint.md l.32'),
  (3, 'Business Translation', 'Translate the fix directly into money',          'alpha_blueprint.md l.22,l.41'),
  (4, 'Signal-to-Noise',      'Filter, do not flatter; polarize on purpose',    'alpha_blueprint.md l.41');

-- ============================================================================
-- 0b. TENANT_BLUEPRINTS  (the "isolated cognitive state" — per-tenant persona)
-- ============================================================================
CREATE TABLE tenant_blueprints (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    persona         TEXT        NOT NULL,
    persona_version TEXT        NOT NULL,
    cognitive_state JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- frameworks, lexical rules, dynamic vars (isolated)
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_blueprint_tenant_persona UNIQUE (tenant_id, persona, persona_version),
    CONSTRAINT ck_blueprint_state_size CHECK (octet_length(cognitive_state::text) < 131072),
    CONSTRAINT uq_blueprint_id_tenant UNIQUE (id, tenant_id)   -- composite FK target
);
CREATE INDEX idx_blueprint_tenant_active ON tenant_blueprints (tenant_id) WHERE is_active;
CREATE TRIGGER trg_blueprint_touch BEFORE UPDATE ON tenant_blueprints
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 1. INGEST_QUEUE  (raw landing + idempotency ledger; written by WORKER)
-- ============================================================================
CREATE TABLE ingest_queue (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id       UUID          NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    source          ingest_source NOT NULL,
    idempotency_key TEXT          NOT NULL,
    raw_payload     JSONB         NOT NULL,
    status          ingest_status NOT NULL DEFAULT 'PENDING',
    attempts        INT           NOT NULL DEFAULT 0,
    last_error      TEXT,
    received_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),
    claimed_at      TIMESTAMPTZ,
    processed_at    TIMESTAMPTZ,
    CONSTRAINT uq_ingest_idempotency UNIQUE (tenant_id, source, idempotency_key),
    CONSTRAINT ck_ingest_payload_size CHECK (octet_length(raw_payload::text) < 65536),
    CONSTRAINT uq_ingest_id_tenant UNIQUE (id, tenant_id)   -- composite FK target
);
CREATE INDEX idx_ingest_actionable ON ingest_queue (tenant_id, status, received_at)
    WHERE status IN ('PENDING', 'FAILED');
CREATE INDEX idx_ingest_stalled ON ingest_queue (tenant_id, claimed_at)
    WHERE status = 'CLAIMED';

-- ============================================================================
-- 2. FEATURE_SAGAS  (orchestration state machine; optimistic-locked)
-- ============================================================================
CREATE TABLE feature_sagas (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),   -- prefer UUIDv7 at scale
    tenant_id   UUID        NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    ingest_id   BIGINT      NOT NULL,
    status      saga_status NOT NULL DEFAULT 'RECEIVED',
    version     BIGINT      NOT NULL DEFAULT 0,
    attempts    INT         NOT NULL DEFAULT 0,
    last_error  TEXT,
    deadline_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_saga_ingest    UNIQUE (ingest_id),
    CONSTRAINT uq_saga_id_tenant UNIQUE (id, tenant_id),          -- composite FK target
    CONSTRAINT fk_saga_ingest_tenant
        FOREIGN KEY (ingest_id, tenant_id)
        REFERENCES ingest_queue (id, tenant_id) ON DELETE RESTRICT  -- saga.tenant == ingest.tenant
);
CREATE INDEX idx_saga_actionable ON feature_sagas (tenant_id, status, updated_at)
    WHERE status IN ('RECEIVED','SYNTHESIZING','APPROVED','DISTRIBUTING','PARTIALLY_DISTRIBUTED','FAILED');
CREATE INDEX idx_saga_deadline ON feature_sagas (tenant_id, deadline_at)
    WHERE status = 'AWAITING_APPROVAL';

-- updated_at + version bump + tenant_id immutability on every write
CREATE OR REPLACE FUNCTION touch_saga() RETURNS trigger AS $$
BEGIN
  IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
    RAISE EXCEPTION 'tenant_id is immutable on feature_sagas';
  END IF;
  NEW.updated_at = now();
  NEW.version    = OLD.version + 1;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_saga_touch BEFORE UPDATE ON feature_sagas
    FOR EACH ROW EXECUTE FUNCTION touch_saga();

-- DB-enforced legal transitions (status is the crash-recovery resume point — guard it)
CREATE OR REPLACE FUNCTION enforce_saga_transition() RETURNS trigger AS $$
BEGIN
  IF NEW.status = OLD.status THEN RETURN NEW; END IF;
  IF (OLD.status, NEW.status) NOT IN (
    ('RECEIVED','SYNTHESIZING'), ('SYNTHESIZING','DRAFTED'),
    ('DRAFTED','AWAITING_APPROVAL'),
    ('AWAITING_APPROVAL','APPROVED'), ('AWAITING_APPROVAL','REJECTED'),
    ('AWAITING_APPROVAL','EXPIRED'),
    ('APPROVED','DISTRIBUTING'),
    ('DISTRIBUTING','PARTIALLY_DISTRIBUTED'), ('DISTRIBUTING','DISTRIBUTED'),
    ('PARTIALLY_DISTRIBUTED','DISTRIBUTED'), ('PARTIALLY_DISTRIBUTED','FAILED'),
    ('SYNTHESIZING','FAILED'), ('DISTRIBUTING','FAILED'),
    ('FAILED','SYNTHESIZING'), ('FAILED','DISTRIBUTING'), ('FAILED','REJECTED')  -- recovery edges
  ) THEN
    RAISE EXCEPTION 'illegal saga transition % -> %', OLD.status, NEW.status;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_saga_fsm BEFORE UPDATE OF status ON feature_sagas
    FOR EACH ROW EXECUTE FUNCTION enforce_saga_transition();

-- ============================================================================
-- 3. CSPE_DRAFTS  (generated drafts + human approval + per-platform distribution)
-- ============================================================================
CREATE TABLE cspe_drafts (
    id                UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID            NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    saga_id           UUID            NOT NULL,
    blueprint_id      UUID            NOT NULL,   -- which cognitive state produced this draft
    persona           TEXT            NOT NULL,   -- denormalized for audit; no default
    persona_version   TEXT            NOT NULL,
    target_platform   target_platform NOT NULL,
    generated_content TEXT            NOT NULL,   -- immutable model output
    edited_content    TEXT,
    approval_status   approval_status NOT NULL DEFAULT 'PENDING',
    dist_status       dist_status     NOT NULL DEFAULT 'PENDING',
    posting_token     UUID            NOT NULL DEFAULT gen_random_uuid(),  -- external-post idempotency
    model_meta        JSONB,
    created_at        TIMESTAMPTZ     NOT NULL DEFAULT now(),
    decided_at        TIMESTAMPTZ,
    posted_at         TIMESTAMPTZ,
    CONSTRAINT uq_draft_saga_platform UNIQUE (saga_id, target_platform),
    CONSTRAINT fk_draft_saga_tenant
        FOREIGN KEY (saga_id, tenant_id)
        REFERENCES feature_sagas (id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_draft_blueprint_tenant
        FOREIGN KEY (blueprint_id, tenant_id)
        REFERENCES tenant_blueprints (id, tenant_id) ON DELETE RESTRICT
);
CREATE INDEX idx_drafts_saga    ON cspe_drafts (tenant_id, saga_id);
CREATE INDEX idx_drafts_pending ON cspe_drafts (tenant_id, created_at)
    WHERE approval_status = 'PENDING';
CREATE INDEX idx_drafts_distrib ON cspe_drafts (tenant_id, dist_status)
    WHERE dist_status IN ('PENDING','POSTING','FAILED');

-- ============================================================================
-- 4. FEATURE_SAGA_EVENTS  (auditable transition history)
-- ============================================================================
CREATE TABLE feature_saga_events (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id   UUID        NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    saga_id     UUID        NOT NULL,
    from_status saga_status,
    to_status   saga_status NOT NULL,
    actor       TEXT,           -- 'system', worker id, or dashboard user id (who approved)
    note        TEXT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_event_saga_tenant
        FOREIGN KEY (saga_id, tenant_id)
        REFERENCES feature_sagas (id, tenant_id) ON DELETE RESTRICT
);
CREATE INDEX idx_saga_events_saga ON feature_saga_events (tenant_id, saga_id, occurred_at);

-- ============================================================================
-- ROW-LEVEL SECURITY  (fail-closed tenant isolation)
--   ENABLE + FORCE on every tenant-scoped table. universal_axioms intentionally
--   excluded (shared reference data). worker_rw must NOT have BYPASSRLS.
-- ============================================================================
ALTER TABLE tenants             ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_blueprints   ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingest_queue        ENABLE ROW LEVEL SECURITY;
ALTER TABLE feature_sagas       ENABLE ROW LEVEL SECURITY;
ALTER TABLE cspe_drafts         ENABLE ROW LEVEL SECURITY;
ALTER TABLE feature_saga_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE tenants             FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_blueprints   FORCE ROW LEVEL SECURITY;
ALTER TABLE ingest_queue        FORCE ROW LEVEL SECURITY;
ALTER TABLE feature_sagas       FORCE ROW LEVEL SECURITY;
ALTER TABLE cspe_drafts         FORCE ROW LEVEL SECURITY;
ALTER TABLE feature_saga_events FORCE ROW LEVEL SECURITY;

-- tenants: the row's own id IS the tenant boundary
CREATE POLICY tenant_isolation_self ON tenants
    USING      (id = current_tenant_id())
    WITH CHECK (id = current_tenant_id());

-- tenant-scoped tables: match on tenant_id (NULL context => no rows => fail closed)
CREATE POLICY tenant_isolation ON tenant_blueprints
    USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id());
CREATE POLICY tenant_isolation ON ingest_queue
    USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id());
CREATE POLICY tenant_isolation ON feature_sagas
    USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id());
CREATE POLICY tenant_isolation ON cspe_drafts
    USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id());
CREATE POLICY tenant_isolation ON feature_saga_events
    USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id());

-- ============================================================================
-- ROLES  (least privilege)
--   worker_rw    : the ONLY runtime writer; subject to RLS (no BYPASSRLS).
--   edge_ingest  : created for documentation; UNUSED (edge writes nothing).
--   tenant_admin : onboarding only; BYPASSRLS so it can create tenant rows that
--                  fail-closed RLS would otherwise forbid. Never handles requests.
-- ============================================================================
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'worker_rw') THEN
    CREATE ROLE worker_rw LOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'edge_ingest') THEN
    CREATE ROLE edge_ingest NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tenant_admin') THEN
    CREATE ROLE tenant_admin LOGIN BYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO worker_rw, tenant_admin;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA public TO worker_rw;
GRANT USAGE                          ON ALL SEQUENCES IN SCHEMA public TO worker_rw;
GRANT SELECT, INSERT, UPDATE ON tenants, tenant_blueprints TO tenant_admin;

COMMIT;

-- ============================================================================
-- RUNTIME CONTRACT (worker, per request transaction):
--   BEGIN;
--   SET LOCAL app.current_tenant = '<resolved-tenant-uuid>';
--   ... idempotent INSERT/UPDATE against the tenant's rows only ...
--   COMMIT;
-- Missing SET LOCAL => current_tenant_id() = NULL => zero rows visible/writable.
-- ============================================================================
