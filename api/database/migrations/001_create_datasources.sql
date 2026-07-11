-- database/migrations/001_create_datasources.sql
--
-- PURPOSE:
--   Creates the datasources table for InsightX's metadata database.
--   Run this against your PostgreSQL metadata DB in production.
--
-- FOR DEVELOPMENT (SQLite):
--   DO NOT run this file. Tables are auto-created by SQLAlchemy at startup via
--   Base.metadata.create_all(). SQLAlchemy handles the dialect differences.
--
-- USAGE (production, PostgreSQL):
--   psql -U your_user -d insightx_meta -f 001_create_datasources.sql
--
-- This file uses PostgreSQL-specific syntax:
--   gen_random_uuid()  -- UUID generation (requires pgcrypto extension)
--   TIMESTAMPTZ        -- Timezone-aware timestamps
--
-- IMPORTANT: This migration is idempotent (uses CREATE TABLE IF NOT EXISTS).
--   Safe to run multiple times.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS datasources (
    -- UUID primary key: avoids sequential ID enumeration attacks
    id                    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Human-readable name, unique per tenant
    name                  VARCHAR(100) NOT NULL,
    tenant_id             VARCHAR(100) NOT NULL,

    -- Database engine identifier
    -- One of: 'postgresql', 'mssql', 'oracle'
    engine                VARCHAR(20)  NOT NULL,

    -- Connection details — not sensitive, stored in plaintext
    host                  VARCHAR(255) NOT NULL,
    port                  INTEGER      NOT NULL,
    database_name         VARCHAR(255) NOT NULL,

    -- Oracle-specific: 'sid' or 'service_name'
    -- NULL for PostgreSQL and MSSQL
    oracle_connection_type VARCHAR(20)  CHECK (oracle_connection_type IN ('sid', 'service_name')),

    -- Default schema/owner scoped in the object browser
    default_schema         VARCHAR(255),

    -- Authentication method label (e.g., 'password', 'wallet', 'kerberos')
    -- Actual credentials are stored encrypted below
    auth_method           VARCHAR(20)  NOT NULL,

    -- AES-256-GCM encrypted JSON: "iv_hex:tag_hex:ciphertext_hex"
    -- NEVER returned via any API endpoint
    encrypted_credentials TEXT         NOT NULL,

    -- TLS configuration
    -- Cert file CONTENT is NOT stored here — only server-side file paths
    tls_enabled           BOOLEAN      NOT NULL DEFAULT FALSE,
    tls_verify_server_cert BOOLEAN     NOT NULL DEFAULT TRUE,
    tls_mode              VARCHAR(20),            -- 'require', 'verify-full', 'encrypt', 'ssl', etc.
    tls_ca_cert_path      VARCHAR(500),           -- Path to uploaded CA cert file
    tls_client_cert_path  VARCHAR(500),           -- Path to uploaded client cert file
    tls_client_key_path   VARCHAR(500),           -- Path to uploaded client key file

    -- Audit metadata
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by            VARCHAR(100),

    -- Last test result (populated by POST /{id}/test)
    last_tested_at        TIMESTAMPTZ,
    last_test_status      VARCHAR(20)  CHECK (last_test_status IN ('success', 'failed')),

    -- Active flag — deactivated connections are preserved but blocked from schema access
    -- Re-test re-activates a deactivated connection if it succeeds
    is_active             BOOLEAN      NOT NULL DEFAULT TRUE,

    -- Enforce uniqueness: two datasources in the same tenant cannot share a name
    CONSTRAINT uq_datasource_name_per_tenant UNIQUE (tenant_id, name)
);

-- Index on tenant_id — almost every query filters by this column
CREATE INDEX IF NOT EXISTS idx_datasources_tenant ON datasources (tenant_id);

-- Auto-update updated_at on every row modification
-- Create the trigger function (idempotent)
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create the trigger (idempotent)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'set_datasources_updated_at'
    ) THEN
        CREATE TRIGGER set_datasources_updated_at
        BEFORE UPDATE ON datasources
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END;
$$;
