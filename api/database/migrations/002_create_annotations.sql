-- database/migrations/002_create_annotations.sql
--
-- PURPOSE:
--   Creates the three annotation tables for M2 Data Annotation.
--   Run this against your PostgreSQL metadata DB in production.
--
-- FOR DEVELOPMENT (SQLite):
--   DO NOT run this file. Tables are auto-created by SQLAlchemy at startup via
--   Base.metadata.create_all(). SQLAlchemy handles the dialect differences.
--
-- USAGE (production, PostgreSQL):
--   psql -U your_user -d insightx_meta -f 002_create_annotations.sql
--
-- IMPORTANT: This migration is idempotent (uses IF NOT EXISTS guards throughout).
--   Safe to run multiple times.

-- Reuses the update_updated_at_column() trigger function created in migration 001.

-- ─────────────────────────────────────────────────────────────────────────────
-- table_annotations
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS table_annotations (
    id            UUID        NOT NULL DEFAULT gen_random_uuid(),
    datasource_id UUID        NOT NULL,
    tenant_id     VARCHAR(100) NOT NULL,
    schema_name   VARCHAR(255) NOT NULL,
    table_name    VARCHAR(255) NOT NULL,
    description   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_table_annotations PRIMARY KEY (id),
    CONSTRAINT uq_table_annotation  UNIQUE (datasource_id, schema_name, table_name)
);

CREATE INDEX IF NOT EXISTS idx_tannot_tenant     ON table_annotations (tenant_id);
CREATE INDEX IF NOT EXISTS idx_tannot_datasource ON table_annotations (datasource_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_table_annotations_updated_at'
          AND tgrelid = 'table_annotations'::regclass
    ) THEN
        CREATE TRIGGER trg_table_annotations_updated_at
        BEFORE UPDATE ON table_annotations
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- column_annotations
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS column_annotations (
    id            UUID        NOT NULL DEFAULT gen_random_uuid(),
    datasource_id UUID        NOT NULL,
    tenant_id     VARCHAR(100) NOT NULL,
    schema_name   VARCHAR(255) NOT NULL,
    table_name    VARCHAR(255) NOT NULL,
    column_name   VARCHAR(255) NOT NULL,
    annotation    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_column_annotations PRIMARY KEY (id),
    CONSTRAINT uq_column_annotation  UNIQUE (datasource_id, schema_name, table_name, column_name)
);

CREATE INDEX IF NOT EXISTS idx_cannot_tenant     ON column_annotations (tenant_id);
CREATE INDEX IF NOT EXISTS idx_cannot_datasource ON column_annotations (datasource_id);
CREATE INDEX IF NOT EXISTS idx_cannot_table      ON column_annotations (datasource_id, schema_name, table_name);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_column_annotations_updated_at'
          AND tgrelid = 'column_annotations'::regclass
    ) THEN
        CREATE TRIGGER trg_column_annotations_updated_at
        BEFORE UPDATE ON column_annotations
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- table_relationships
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS table_relationships (
    id                UUID        NOT NULL DEFAULT gen_random_uuid(),
    datasource_id     UUID        NOT NULL,
    tenant_id         VARCHAR(100) NOT NULL,
    schema_name       VARCHAR(255) NOT NULL,
    from_table        VARCHAR(255) NOT NULL,
    from_column       VARCHAR(255) NOT NULL,
    to_table          VARCHAR(255) NOT NULL,
    to_column         VARCHAR(255) NOT NULL,
    relationship_type VARCHAR(20)  NOT NULL,
    is_discovered     BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_table_relationships  PRIMARY KEY (id),
    CONSTRAINT chk_relationship_type   CHECK (relationship_type IN ('many-to-one', 'one-to-one', 'many-to-many'))
);

CREATE INDEX IF NOT EXISTS idx_trel_tenant     ON table_relationships (tenant_id);
CREATE INDEX IF NOT EXISTS idx_trel_datasource ON table_relationships (datasource_id);
CREATE INDEX IF NOT EXISTS idx_trel_schema     ON table_relationships (datasource_id, schema_name);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_table_relationships_updated_at'
          AND tgrelid = 'table_relationships'::regclass
    ) THEN
        CREATE TRIGGER trg_table_relationships_updated_at
        BEFORE UPDATE ON table_relationships
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;
