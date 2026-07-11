# M1 — Data Source Onboarding: Technical Implementation Documentation (v2)

> **Scope:** This document covers the complete technical implementation of Feature 107125 — M1 Data Source Onboarding — as it stands in the current codebase. It covers all five user stories, both frontend and backend, reflecting the latest architectural changes including the redesigned single-page frontend, the Strategy-pattern schema inspector, typed credential discriminated unions, Keycloak authentication scaffolding, and the three new API endpoints added since v1.

---

## Table of Contents

1. [System Overview & Architecture](#1-system-overview--architecture)
2. [Project Directory Structure](#2-project-directory-structure)
3. [Backend Architecture Deep-Dive](#3-backend-architecture-deep-dive)
4. [Frontend Architecture Deep-Dive](#4-frontend-architecture-deep-dive)
5. [US 107147 — Database Connector Registration](#5-us-107147--database-connector-registration)
6. [US 107148 — Authentication Configuration](#6-us-107148--authentication-configuration)
7. [US 107149 — TLS/SSL Encryption Configuration](#7-us-107149--tlsssl-encryption-configuration)
8. [US 107150 — Connection Test & Validation](#8-us-107150--connection-test--validation)
9. [US 107151 — Permission-Scoped Object Browser](#9-us-107151--permission-scoped-object-browser)
10. [Cross-Cutting Concerns](#10-cross-cutting-concerns)
11. [API Reference](#11-api-reference)
12. [Data Flow Diagrams](#12-data-flow-diagrams)

---

## 1. System Overview & Architecture

### 1.1 What is InsightX

InsightX is an agentic reporting platform designed for enterprise banking. It allows analysts and branch officers to connect enterprise databases, query them using plain natural language, and produce shareable visualisations — all without writing SQL. M1 is the foundational module: without registered, validated database connections, every downstream capability (NL-to-SQL, data dictionary, insights) cannot function.

### 1.2 High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Browser (Next.js :3000)                       │
│                                                                      │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────────────┐ │
│  │  Keycloak  │  │  AppShell    │  │    /datasource page           │ │
│  │  Auth      │  │  Sidebar     │  │  ┌────────────────────────┐   │ │
│  │  Provider  │  │  Topbar      │  │  │  CredentialModal        │   │ │
│  └────────────┘  └──────────────┘  │  │  (Register + Test)      │   │ │
│         │                          │  └────────────────────────┘   │ │
│         │  Bearer token injected   │  ┌────────────────────────┐   │ │
│         │  by auth-fetch.utils.ts  │  │  TableBrowserView       │   │ │
│         └──────────────────────────│  │  (Browse + Search)      │   │ │
│                                    │  └────────────────────────┘   │ │
│                                    └──────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
                           │
               /api/* → :8000 (dev proxy via next.config.ts)
               /api/* → :8000 (NGINX reverse-proxy in production)
                           │
┌──────────────────────────────────────────────────────────────────────┐
│                   FastAPI Backend (:8000)                             │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  Router (10 endpoints)                                           │ │
│  │  POST /test   POST /upload  POST /   GET /                      │ │
│  │  POST /{id}/test  PATCH /{id}/deactivate  GET /{id}/schema      │ │
│  │  GET /{id}/tables  GET /{id}/search   DELETE /{id}              │ │
│  └──────────────────────┬──────────────────────────────────────────┘ │
│                         │                                            │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  Service Layer (business logic, no HTTP)                         │ │
│  └──────┬────────────┬────────────────┬───────────────────────────┘ │
│         │            │                │                              │
│  ┌──────▼──────┐ ┌───▼──────────┐ ┌──▼──────────────────────────┐  │
│  │ Credential  │ │ Connection   │ │ Schema Inspector              │  │
│  │ Encryptor   │ │ Tester       │ │ (Strategy Pattern)           │  │
│  │ AES-256-GCM │ │ (Dispatcher) │ │ PostgresDriver / OracleDriver│  │
│  └─────────────┘ └──────┬───────┘ │ / MSSQLDriver                │  │
│                         │         └─────────────────────────────┘  │
│                    ┌────▼──────────────────┐                        │
│                    │   Per-Engine Drivers   │                        │
│                    │  postgres / oracle /   │                        │
│                    │  mssql                 │                        │
│                    └────────────────────────┘                        │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  SQLAlchemy ORM + AsyncSession                                   │ │
│  └──────────────────────────┬──────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                              │
           ┌──────────────────┴──────────────────────┐
           │                                          │
    ┌──────▼──────┐                      ┌────────────▼──────────┐
    │ Metadata DB  │                      │  Target Datasources    │
    │ (PostgreSQL  │                      │  Oracle / PG / MSSQL  │
    │  or SQLite)  │                      │  (runtime connections) │
    └─────────────┘                      └───────────────────────┘
```

### 1.3 Two Distinct Database Connections

A critical design point that must be understood before reading any further:

| Connection                        | Purpose                                                        | How configured                                                   | Sensitive?                                |
| --------------------------------- | -------------------------------------------------------------- | ---------------------------------------------------------------- | ----------------------------------------- |
| **Metadata DB**                   | Stores datasource registrations, encrypted creds, test history | `DATABASE_URL` env var, set once in `.env`                       | No — it's an infrastructure concern       |
| **Target Datasource connections** | The user's Oracle/PG/MSSQL databases they want to query        | Created at runtime from user input; credentials stored encrypted | Yes — encrypted at rest using AES-256-GCM |

Target datasource connections are created **dynamically** from user input. They are never configured in `.env`. Their credentials are encrypted and stored in the metadata DB, then decrypted **only in memory** when a live connection is needed for re-testing or schema discovery.

### 1.4 Key Design Decisions (v2 changes)

Since the v1 implementation, the following architectural decisions were made:

**Backend:**

- The schema inspector was rewritten from a procedural dispatch table to an **Abstract Base Class + Strategy Pattern** (`EngineDriver` abstract class with `PostgresDriver`, `OracleDriver`, `MSSQLDriver` subclasses). This enables each driver to own its own connection, query, and result-mapping logic cleanly.
- Three new API endpoints were added: `GET /{id}/tables` (paginated table browser), `GET /{id}/search` (table name search), `DELETE /{id}` (datasource removal).
- Pydantic credential schemas were upgraded from a flat `Dict[str, Any]` to a **discriminated union** (`PasswordCredentials | LdapCredentials | WalletCredentials | ...`), giving full compile-time type safety and self-documenting API schemas.
- A `default_schema` field was added to `DatasourcePayload` and the ORM model, allowing users to specify which schema/owner the object browser should scope to.
- The router gained a `get_current_user` dependency stub (returns a hardcoded dev user) as a Keycloak integration placeholder for M10.
- File uploads are now validated at three layers: allowed upload types, allowed file extensions, and file size (configurable via `MAX_UPLOAD_SIZE_MB`). Files are written asynchronously using `aiofiles`.

**Frontend:**

- The 5-step wizard was replaced with a **single-modal pattern** (`CredentialModal`). All connection, auth, and TLS fields live in one vertically-scrollable modal, test and save are triggered by buttons in the modal footer.
- The entire frontend was **restyled** with a custom CSS design system (`design.css`) using CSS custom properties (oklch colour space, semantic tokens). Tailwind is still imported but `design.css` governs all InsightX-specific components.
- A **full application shell** was added: `AppShell.tsx` (layout wrapper), `Sidebar.tsx` (navigation), `Topbar.tsx` (breadcrumb header), and modal overlays for Settings, Notifications, and Profile.
- **Keycloak authentication** was scaffolded: `AuthProvider.tsx`, `keycloak.ts`, `auth-fetch.utils.ts`, and `webcrypto-polyfill.ts` (for non-HTTPS development environments).
- Data fetching was migrated from manual `fetch()` calls to **SWR** (`useSWR` for reads, `useSWRMutation` for writes). All API calls flow through `lib/utils/fetch.utils.ts` and `lib/utils/auth-fetch.utils.ts`.
- A complete `TableBrowserView` component was added inside `datasource/page.tsx`, featuring client-side pagination, server-side name search, re-test, and sync.
- The `datasource` route was renamed from `/datasources` (plural) to `/datasource` (singular) to match the navigation label.

---

## 2. Project Directory Structure

```
InsightX/
├── api/                                    ← FastAPI backend (Python 3.11+)
│   ├── .env.example                        ← Environment variable template
│   ├── requirements.txt                    ← Python dependencies
│   ├── app/
│   │   ├── main.py                         ← App factory, lifespan, CORS, routers
│   │   ├── core/
│   │   │   ├── config.py                   ← Pydantic Settings (all env vars)
│   │   │   └── engines_config.py           ← Per-engine auth capability map
│   │   ├── db/
│   │   │   ├── base.py                     ← SQLAlchemy DeclarativeBase
│   │   │   ├── session.py                  ← Async engine + session factory
│   │   │   └── models/
│   │   │       ├── datasource.py           ← Datasource ORM model (13 columns)
│   │   │       └── annotation.py           ← TableAnnotation, ColumnAnnotation, TableRelationship,ORM
│   │   └── modules/
│   │       ├── datasources/
│   │       │   ├── router.py               ← 11 FastAPI endpoints
│   │       │   ├── schemas.py              ← Pydantic request/response models
│   │       │   ├── service.py              ← Business logic (no HTTP); cascade-deletes
│   │       │   ├── connection_tester.py    ← Dispatcher + error classifier
│   │       │   ├── credential_encryptor.py ← AES-256-GCM encrypt/decrypt
│   │       │   ├── schema_inspector.py     ← Strategy pattern: inspect/browse/search/columns/discover_relationships
│   │       │   └── drivers/
│   │       │       ├── postgres_driver.py  ← asyncpg connector
│   │       │       ├── oracle_driver.py    ← python-oracledb connector
│   │       │       └── mssql_driver.py     ← pyodbc connector (threadpool)
│   │       └── annotations/
│   │           ├── router.py               ← Annotation CRUD + relationship endpoints (5 routes)
│   │           ├── schemas.py              ← Pydantic models for M2 (annotations + relationships)
│   │           └── service.py              ← Annotation logic: upsert, FK sync, cascade delete
│   └── database/
│       └── migrations/
│           ├── 001_create_datasources.sql  ← Idempotent DDL + triggers
│           └── 002_create_annotations.sql  ← table_annotations, column_annotations, etc
│
├── web/                                    ← Next.js 16 frontend (TypeScript)
│   ├── next.config.ts                      ← Dev proxy rewrites + allowedDevOrigins
│   ├── tsconfig.json                       ← @/* alias → web/ root
│   ├── eslint.config.mjs                   ← ESLint with next/core-web-vitals
│   ├── app/
│   │   ├── globals.css                     ← Tailwind import, theme vars, spinner
│   │   ├── design.css                      ← InsightX design system (all tokens + components)
│   │   ├── layout.tsx                      ← Root layout: design.css + AppShell
│   │   ├── page.tsx                        ← Home → redirects to /insight
│   │   ├── providers.tsx                   ← Redux + Keycloak auth providers
│   │   ├── component/
│   │   │   ├── AppShell.tsx                ← Layout: sidebar + topbar + modals
│   │   │   ├── Sidebar.tsx                 ← Navigation + chat history
│   │   │   ├── Topbar.tsx                  ← Page title + breadcrumb
│   │   │   ├── Icon.tsx                    ← SVG icon set (inline paths, no deps)
│   │   │   ├── DBLogo.tsx                  ← Engine logo with fallback avatar
│   │   │   ├── AuthProvider.tsx            ← Keycloak PKCE init + token refresh
│   │   │   ├── BarChart.tsx                ← Simple bar chart SVG
│   │   │   ├── LineChart.tsx               ← Simple line/area chart SVG
│   │   │   ├── SqlBlock.tsx                ← Collapsible SQL code block
│   │   │   ├── Topbar.tsx                  ← Page header
│   │   │   ├── Modals.tsx                  ← Settings, Notifications, Profile drawers
│   │   │   └── ReduxTest.tsx               ← Dev tool (Redux counter smoke test)
│   │   ├── datasource/                     ← /datasource route
│   │   │   └── page.tsx                    ← Complete M1 UI (modal + browser + list)
│   │   ├── dashboard/page.tsx              ← Dashboard placeholder
│   │   ├── insight/page.tsx                ← NL chat interface (M3 placeholder)
│   │   ├── users/page.tsx                  ← User management (M10 placeholder)
│   │   ├── glossary/page.tsx               ← Glossary (M8 placeholder)
│   │   └── developers/page.tsx             ← API keys + docs (M11 placeholder)
│   ├── config/
│   │   ├── engines.ts                      ← Engine metadata (ports, auth methods, TLS)
│   │   └── url.config.ts                   ← All API URL builders + Keycloak config
│   ├── hooks/                              ← Shared custom hooks (to be expanded in M2+)
│   └── lib/
│       ├── types/
│       │   ├── types.ts                    ← App-wide TS types
│       │   └── interface/features/
│       │       ├── auth.interface.ts       ← Keycloak user info types
│       │       └── datasource.interface.ts ← All M1 TS interfaces
│       ├── utils/
│       │   ├── fetch.utils.ts              ← Typed HTTP client (get/post/put/del)
│       │   └── auth-fetch.utils.ts         ← Keycloak Bearer token injection
│       ├── redux/
│       │   ├── store.ts
│       │   ├── hooks.ts
│       │   └── features/counter/counterSlice.ts
│       ├── keycloak.ts                     ← Keycloak singleton factory
│       └── webcrypto-polyfill.ts           ← SHA-256 + randomUUID for HTTP dev
│
├── infra/                                  ← Placeholder (NGINX, IaC)
├── job/                                    ← Placeholder (background workers)
├── mcp/                                    ← Placeholder (model control plane)
├── README.md
└── CLAUDE.md
```

---

## 3. Backend Architecture Deep-Dive

### 3.1 Application Factory and Lifespan (`app/main.py`)

The application factory follows the **FastAPI lifespan** pattern introduced in FastAPI 0.93. The lifespan context manager handles all startup and shutdown logic.

```
@asynccontextmanager
async def lifespan(app):
    # ── STARTUP ──
    1. Assert EngineType enum ↔ ENGINES config are in sync
       (prevents silent mismatches when adding new engines)
    2. create_all() — create tables that don't exist yet
       (safe to run repeatedly: does NOT drop or modify existing columns)
    3. Migration shim — ALTER TABLE to add new columns
       (wrapped in try/except; silently skips if column already exists)
    yield
    # ── SHUTDOWN ──
    4. engine.dispose() — close all pooled connections gracefully
```

**Why the migration shim?** SQLAlchemy's `create_all()` only creates missing _tables_, not missing _columns_ on existing tables. The shim handles the `default_schema` column added after initial deployment without requiring a full migration runner. In production, a proper migration tool (Alembic) would replace this.

**EngineType/ENGINES sync assertion:**

```python
assert set(e.value for e in EngineType) == set(ENGINES), (
    "EngineType enum and ENGINES config are out of sync"
)
```

This fires immediately on startup if a developer adds a new engine to one config but forgets the other. The application refuses to start rather than running with inconsistent state.

### 3.2 Configuration Management (`app/core/config.py`)

All runtime configuration is centralised in a single Pydantic `Settings` class. No other file reads `os.environ` directly.

```python
class Settings(BaseSettings):
    # Metadata DB
    database_url: str = "postgresql+asyncpg://..."

    # Encryption
    credential_encryption_key: str   # ← No default; crashes if missing

    # Upload storage
    secure_files_dir: str = "./secure-uploads"
    max_upload_size_mb: int = 5

    # Connection pool
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # Keycloak (M10)
    keycloak_url: str = ""
    keycloak_realm: str = "insightx"
    keycloak_client_id: str = "insightx-backend"
    keycloak_client_secret: str = ""
    introspect_cache_ttl_seconds: int = 30
```

`@lru_cache()` on `get_settings()` ensures the Settings object is constructed exactly once (reads `.env` exactly once), and the same instance is returned to every caller. The module-level `settings = get_settings()` alias means other modules can do `from app.core.config import settings` without re-constructing.

### 3.3 Engine Capabilities (`app/core/engines_config.py`)

This module defines which authentication methods each engine supports, using a Python `TypedDict`:

```python
class EngineCapabilities(TypedDict):
    supported_auth_methods: list[str]

ENGINES: dict[str, EngineCapabilities] = {
    "postgresql": { "supported_auth_methods": ["password", "ldap"] },
    "oracle":     { "supported_auth_methods": ["password", "wallet", "kerberos"] },
    "mssql":      { "supported_auth_methods": ["password", "windows", "azure_ad"] },
}
```

This dict is used by the Pydantic schema's `validate_engine_and_auth` validator to reject invalid engine+auth combinations _before_ they reach any driver code. The startup assertion in `main.py` ensures this dict always contains exactly the engines listed in the `EngineType` enum.

The frontend mirrors this in `web/config/engines.ts`, but adds UI-specific metadata (default ports, TLS modes, labels). Both must be kept in sync when adding new engines.

### 3.4 Database Session Factory (`app/db/session.py`)

The session factory auto-detects SQLite vs PostgreSQL from the `DATABASE_URL` string and applies the appropriate connection pool configuration:

```
"sqlite" in DATABASE_URL?
├── YES: StaticPool + check_same_thread=False
│        (SQLite only supports one concurrent connection; StaticPool enforces this)
└── NO:  pool_pre_ping=True + pool_size + max_overflow
         (PostgreSQL: TCP connections may die silently; pre_ping detects this)
```

`get_db()` is an async generator yielding an `AsyncSession` as a FastAPI dependency. The session commit/rollback is handled here, not in the router or service:

```python
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()   # ← Commits if route handler returned normally
        except Exception:
            await session.rollback() # ← Rolls back if route handler raised
            raise
```

This means route handlers and service functions only need to call `await db.flush()` (sends SQL to the DB within the transaction) rather than `await db.commit()` (which would also close the transaction).

### 3.5 ORM Model (`app/db/models/datasource.py`)

The `Datasource` model maps directly to the `datasources` table:

```
datasources table:
├── id (UUID, PK)                    — Python-generated uuid4, not DB-generated
├── name (VARCHAR 100)               — Human-readable name
├── tenant_id (VARCHAR 100)          — Multi-tenancy isolation key
├── engine (VARCHAR 20)              — "postgresql" | "oracle" | "mssql"
├── host, port, database_name        — Plaintext connection details
├── oracle_connection_type           — "sid" | "service_name" | NULL
├── auth_method (VARCHAR 20)         — Credential format label
├── encrypted_credentials (TEXT)     — "iv_hex:tag_hex:ciphertext_hex"
├── default_schema (VARCHAR 255)     — Schema/owner for object browser
├── tls_enabled (BOOLEAN)
├── tls_verify_server_cert (BOOLEAN)
├── tls_mode (VARCHAR 20)
├── tls_ca_cert_path (VARCHAR 500)   — Server-side file path (not content)
├── tls_client_cert_path (VARCHAR 500)
├── tls_client_key_path (VARCHAR 500)
├── created_at, updated_at (TIMESTAMPTZ)
├── created_by (VARCHAR 100)
├── last_tested_at (TIMESTAMPTZ)
├── last_test_status (VARCHAR 20)    — "success" | "failed" | NULL
└── is_active (BOOLEAN)              — Deactivated connections (default TRUE)

Constraints:
- UNIQUE (tenant_id, name)
- CHECK oracle_connection_type IN ('sid', 'service_name')
- CHECK last_test_status IN ('success', 'failed')
- INDEX ON tenant_id
```

Three `CheckConstraint`s enforce data integrity at the database level, so even direct SQL inserts can't violate business rules.

### 3.6 Pydantic Schemas (`app/modules/datasources/schemas.py`)

This is one of the most important files in the backend. It defines the contract between the API and its clients.

#### 3.6.1 Discriminated Union Credentials

The credential type uses Pydantic's **discriminated union** pattern. Each credential sub-type has a `method` field with a `Literal` type, which Pydantic uses as the discriminator:

```python
class PasswordCredentials(BaseModel):
    method: Literal["password"]
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)

class WalletCredentials(BaseModel):
    method: Literal["wallet"]
    wallet_location: str = Field(min_length=1)  # Server-side path after upload
    wallet_password: Optional[str] = None
    username: Optional[str] = None

class KerberosCredentials(BaseModel):
    method: Literal["kerberos"]
    principal: str = Field(min_length=1)
    keytab_path: str = Field(min_length=1)  # Server-side path after upload

class WindowsCredentials(BaseModel):
    method: Literal["windows"]
    domain: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

class AzureAdCredentials(BaseModel):
    method: Literal["azure_ad"]
    access_token: str = Field(min_length=1)

Credentials = Annotated[
    Union[PasswordCredentials, LdapCredentials, WalletCredentials,
          KerberosCredentials, WindowsCredentials, AzureAdCredentials],
    Field(discriminator="method"),
]
```

**Why discriminated unions?** The v1 implementation used `Dict[str, Any]` for credentials, which:

- Had no validation (an Oracle wallet payload could omit `wallet_location` and pass through)
- Generated unhelpful API documentation (just "object")
- Required runtime checks in the driver

With discriminated unions:

- Each credential format is fully validated
- The OpenAPI schema shows exactly what fields each auth method requires
- Driver code can use typed attribute access (`credentials.wallet_location`) instead of `.get()`

#### 3.6.2 The `inject_credential_method` Pre-Validator

The public API surface sends `auth_method` at the payload level, not inside the credentials dict. But Pydantic needs `method` inside the credentials dict to select the correct union member. The pre-validator bridges this:

```python
@model_validator(mode="before")
@classmethod
def inject_credential_method(cls, data: Any) -> Any:
    """
    Clients send:    { "auth_method": "password", "credentials": { "username": "x", "password": "y" } }
    Pydantic needs:  { "auth_method": "password", "credentials": { "method": "password", "username": "x", ... } }
    """
    auth_method = data.get("auth_method")
    credentials = data.get("credentials")
    if auth_method and isinstance(credentials, dict) and "method" not in credentials:
        data = {**data, "credentials": {**credentials, "method": auth_method}}
    return data
```

This keeps the API client-facing shape clean (no redundant `credentials.method` field) while satisfying Pydantic's discriminator requirement internally.

#### 3.6.3 Cross-Field Validation

A `model_validator(mode="after")` runs after all field-level validators pass, enforcing business rules:

```python
@model_validator(mode="after")
def validate_engine_and_auth(self) -> "DatasourcePayload":
    # Rule 1: oracle_connection_type required for Oracle, forbidden for others
    if self.engine == EngineType.oracle:
        if self.oracle_connection_type is None:
            raise ValueError("oracle_connection_type is required for Oracle")
    else:
        if self.oracle_connection_type is not None:
            raise ValueError("oracle_connection_type must not be set for non-Oracle")

    # Rule 2: auth_method must be in engine's supported list
    valid_methods = ENGINES[self.engine.value]["supported_auth_methods"]
    if self.auth_method.value not in valid_methods:
        raise ValueError(f"'{self.auth_method.value}' not valid for '{self.engine.value}'")

    # Rule 3: credentials.method must match auth_method
    if self.credentials.method != self.auth_method.value:
        raise ValueError("credentials.method must match auth_method")

    return self
```

Any violation raises a `ValueError` which FastAPI automatically converts to an HTTP 422 Unprocessable Entity with a structured error body explaining which field failed and why.

#### 3.6.4 Response Schemas

The response schemas are just as important as the request schemas. `DatasourceResponse` deliberately omits all sensitive fields:

```python
class DatasourceResponse(BaseModel):
    id, name, tenant_id, engine, host, port, database_name     # ← Safe plaintext
    oracle_connection_type, auth_method, default_schema         # ← Labels only
    tls_enabled, tls_mode                                       # ← Config labels
    created_at, updated_at, created_by                          # ← Audit
    last_tested_at, last_test_status                            # ← Test history

    # Presence flags — indicate "something is configured" without revealing values
    has_credentials: bool = False   # Always True for saved datasources
    has_ca_cert:     bool = False   # True if ca_cert_path is set
    has_client_cert: bool = False   # True if client_cert_path is set
```

`encrypted_credentials`, `tls_ca_cert_path`, `tls_client_cert_path`, and `tls_client_key_path` are **never included** in any API response.

### 3.7 Router (`app/modules/datasources/router.py`)

The router declares 9 FastAPI endpoints, each with a clear single responsibility:

#### 3.7.1 Authentication Dependency Stub

```python
async def get_current_user() -> dict:
    return {
        "id":        "dev-user-001",
        "tenant_id": "dev-tenant-001",
    }
```

This stub returns a hardcoded development user. In M10, this will be replaced with a Keycloak token introspection dependency that:

1. Reads the `Authorization: Bearer <token>` header
2. Calls the Keycloak introspection endpoint (with a TTL cache)
3. Returns `{"id": sub_claim, "tenant_id": tenant_from_roles_or_claims}`

The stub's existence in every route via `CurrentUser = Annotated[dict, Depends(get_current_user)]` means all routes are already wired for auth — swapping the stub for real auth in M10 requires changing only one function.

#### 3.7.2 Secure File Upload Validation

The upload endpoint implements three-layer validation before writing anything to disk:

**Layer 1: Upload type whitelist**

```python
_ALLOWED_UPLOAD_TYPES = {"ca_cert", "client_cert", "client_key", "wallet", "keytab"}
if type not in _ALLOWED_UPLOAD_TYPES:
    raise HTTPException(400, "Invalid upload type")
```

**Layer 2: File extension whitelist**

```python
_ALLOWED_EXTENSIONS = {".pem", ".crt", ".cer", ".key", ".p12", ".sso", ".keytab", ".kt"}
if Path(file.filename).suffix.lower() not in _ALLOWED_EXTENSIONS:
    raise HTTPException(400, "File type not permitted")
```

**Layer 3: File size limit (checked before disk write)**

```python
contents = await file.read()   # Read entire body into memory first
if len(contents) > settings.max_upload_size_mb * 1024 * 1024:
    raise HTTPException(413, "File exceeds the limit")
```

Reading the full file before the size check means oversized files are rejected without partial writes. The tradeoff is that small files (certs, keytabs, wallets — all well under 1 MB) consume memory briefly.

**Non-predictable filename generation:**

```python
safe_filename = f"{tenant_id}-{ts}-{random_hex}{ext}"
```

Format: `{tenant}-{timestamp_ms}-{4_random_bytes_hex}.{ext}`. This prevents:

- Filename collisions between tenants
- Filename guessing (4 random bytes = 4 billion possibilities)
- Path traversal (tenant_id is validated via the auth dep)

**Async file write with `aiofiles`:**

```python
async with aiofiles.open(dest_path, "wb") as out:
    await out.write(contents)
```

Using `aiofiles` avoids blocking the FastAPI event loop during the disk write. For small files this is rarely a bottleneck, but the pattern scales correctly if large file support is added later.

### 3.8 Service Layer (`app/modules/datasources/service.py`)

The service layer is the business logic hub. It has no knowledge of HTTP — it receives typed Python objects and returns typed Python dicts.

#### 3.8.1 `create_datasource`

```
Input:  DatasourcePayload + tenant_id + user_id
Output: dict (from _mask_sensitive_fields)

1. SELECT WHERE name + tenant_id → raise ValueError if name already taken
2. encrypt(credentials.model_dump()) → encrypted_creds string
3. Construct Datasource ORM model from payload
4. db.add(model) + db.flush()  (commit is in get_db())
5. Return _mask_sensitive_fields(model)
```

`credentials.model_dump()` serialises the typed Pydantic credential object back to a plain dict before encryption. This ensures the stored format is stable and doesn't include any Pydantic-specific metadata.

#### 3.8.2 `_datasource_runtime_config`

This private function is the bridge between stored state and live connections. It decrypts credentials and reassembles a config dict that drivers understand:

```python
def _datasource_runtime_config(datasource: Datasource) -> dict:
    plaintext_credentials = decrypt(datasource.encrypted_credentials)
    return {
        "engine":                 datasource.engine,
        "host":                   datasource.host,
        "port":                   datasource.port,
        "database":               datasource.database_name,
        "oracle_connection_type": datasource.oracle_connection_type,
        "auth_method":            datasource.auth_method,
        "credentials":            plaintext_credentials,
        "tls": {
            "enabled":            datasource.tls_enabled,
            "verify_server_cert": datasource.tls_verify_server_cert,
            "mode":               datasource.tls_mode,
            "ca_cert_path":       datasource.tls_ca_cert_path,
            "client_cert_path":   datasource.tls_client_cert_path,
            "client_key_path":    datasource.tls_client_key_path,
        } if datasource.tls_enabled else {"enabled": False},
    }
```

This config dict is the **common language** between the service and all drivers (connection tester, schema inspector). Both `retest_saved_datasource` and `get_datasource_schema` call this function to produce the same config format.

#### 3.8.3 `_mask_sensitive_fields`

Every service function that returns datasource data goes through this function. It explicitly constructs the response dict, never using ORM model serialisation directly, which would risk accidentally including sensitive fields if SQLAlchemy's `__dict__` were used:

```python
def _mask_sensitive_fields(datasource: Datasource) -> dict:
    return {
        # Safe fields
        "id":       str(datasource.id),
        "name":     datasource.name,
        # ... all safe fields ...

        # Presence flags — never the actual values
        "has_credentials": bool(datasource.encrypted_credentials),
        "has_ca_cert":     bool(datasource.tls_ca_cert_path),
        "has_client_cert": bool(datasource.tls_client_cert_path),

        # INTENTIONALLY OMITTED: encrypted_credentials, tls_*_path
    }
```

---

## 4. Frontend Architecture Deep-Dive

### 4.1 Design System (`app/design.css`)

The InsightX frontend uses a **CSS custom property design system** declared in `design.css`. This file defines all design tokens and component classes used across the application. It is imported in `app/layout.tsx` and applies globally.

#### 4.1.1 Colour Tokens

All colours use the **OKLCH colour space**, which provides perceptually uniform lightness steps:

```css
:root {
  /* Surfaces — warm tinted neutrals */
  --bg: oklch(0.984 0.006 75); /* Page background — very warm off-white */
  --surface: oklch(1 0 0); /* Pure white for cards */
  --surface-2: oklch(0.972 0.006 75); /* Secondary panels */

  /* Text */
  --text: oklch(0.29 0.014 65); /* Primary — dark warm brown */
  --text-muted: oklch(0.52 0.012 65); /* Secondary */
  --text-faint: oklch(0.66 0.01 65); /* Captions */

  /* Accent — friendly blue */
  --accent: oklch(0.62 0.165 262);
  --accent-hover: oklch(0.555 0.18 262);
  --accent-soft: oklch(0.955 0.032 262); /* Tinted background */
  --accent-text: oklch(0.5 0.16 262); /* Text on white bg */
  --accent-ring: oklch(0.62 0.165 262 / 0.35); /* Focus ring */

  /* Semantic */
  --success: oklch(0.62 0.13 158); /* Green */
  --warn: oklch(0.7 0.12 65); /* Amber */
  --danger: oklch(0.585 0.18 22); /* Red */
  --purple: oklch(0.58 0.16 300); /* Purple */
}
```

This warm palette is intentionally non-standard for enterprise software — it creates an approachable, trustworthy feel rather than the cold grey-on-white of typical database tools.

#### 4.1.2 Component Classes

`design.css` defines the complete component vocabulary:

- **Layout:** `.ix` (app shell), `.sidebar`, `.ix-main`, `.ix-page`
- **Navigation:** `.nav-item`, `.nav-item.active`, `.nav-badge`
- **Primitives:** `.card`, `.pill`, `.pill-green/red/blue/purple`, `.btn`, `.btn-primary/ghost/subtle`
- **Forms:** `.field`, `.input`, `.select`, `.toggle`
- **Data source:** `.conn-card`, `.connected-row`, `.table-card`, `.barchart`
- **Insight chat:** `.chat-empty`, `.msg-user`, `.msg-assistant`, `.composer`
- **Modals:** `.overlay`, `.modal`, `.drawer-wrap`, `.drawer`
- **Animations:** `.fade-up`, `.fade-in`

Tailwind CSS (`@import "tailwindcss"` in `globals.css`) is available but only used for utility classes not covered by `design.css`. The two coexist because `design.css` is imported after `globals.css` in `layout.tsx`, ensuring `design.css` classes take precedence.

### 4.2 Application Shell (`component/AppShell.tsx`)

Every page in InsightX is wrapped by `AppShell`. The shell manages three things:

1. **Layout composition:** `<Sidebar>` (left) + `.ix-main` wrapper (right, containing `<Topbar>` and `<main>`)
2. **Modal state:** A single `modal` state variable controls which overlay is shown (`'settings' | 'notifications' | 'profile' | null`)
3. **Modal rendering:** Conditionally renders `<SettingsModal>`, `<NotificationsDrawer>`, or `<ProfileDrawer>` based on `modal` state

```typescript
export default function AppShell({ children }) {
  const [modal, setModal] = useState<ModalType>(null);

  return (
    <div className="ix">
      <Sidebar onOpenModal={setModal} />  {/* ← passes setModal to sidebar */}
      <div className="ix-main">
        <Topbar />
        <main className="ix-page">{children}</main>
      </div>
      {modal === 'settings'      && <SettingsModal ... />}
      {modal === 'notifications' && <NotificationsDrawer ... />}
      {modal === 'profile'       && <ProfileDrawer ... />}
    </div>
  );
}
```

`AppShell` is a **client component** (`'use client'`). `layout.tsx` (a server component) renders it with `{children}` as the page content.

### 4.3 Authentication Architecture (`component/AuthProvider.tsx`)

Keycloak authentication is scaffolded for M1 and will be fully activated in M10. The current implementation:

#### 4.3.1 Keycloak Singleton (`lib/keycloak.ts`)

```typescript
let instance: Keycloak | null = null;

export function getKeycloak(): Keycloak {
  if (!instance) {
    instance = new Keycloak({
      url: keycloakURL,
      realm: keycloakRealm,
      clientId: keycloakClientId,
    });
  }
  return instance;
}
```

The singleton pattern prevents `keycloak-js` from being initialised twice. React StrictMode mounts effects twice in development, which would cause `init()` to be called twice and fail. The singleton handles this by re-using the same instance.

#### 4.3.2 PKCE S256 Initialisation

```typescript
initPromise = keycloak.init({
  onLoad: "login-required", // Redirect to Keycloak login if not authenticated
  pkceMethod: "S256", // PKCE with SHA-256 code challenge
  checkLoginIframe: false, // Disable silent login check (causes issues on some browsers)
});
```

`login-required` means the app will redirect to the Keycloak login page immediately if no valid session exists. All child components (including the datasource page) are only rendered after authentication succeeds.

#### 4.3.3 WebCrypto Polyfill (`lib/webcrypto-polyfill.ts`)

Keycloak's PKCE S256 implementation requires `crypto.subtle.digest('SHA-256')` and `crypto.randomUUID()`. These Web Crypto APIs are only available in **secure contexts** (HTTPS or localhost). On a LAN IP over HTTP (common during development: `http://10.11.200.99:3000`), they are absent.

The polyfill provides pure-JavaScript implementations:

- **SHA-256:** A full FIPS 180-4 implementation using `Uint32Array` operations
- **`crypto.randomUUID()`:** Uses `crypto.getRandomValues()` (available over HTTP) to build an RFC 4122 v4 UUID

The polyfill:

1. Checks if `crypto.subtle.digest` and `crypto.randomUUID` are already present
2. If absent, injects implementations via `Object.defineProperty`
3. If that fails (read-only prototype), replaces `window.crypto` entirely while preserving the native `getRandomValues` for entropy

This allows full Keycloak PKCE authentication to work over HTTP on LAN development environments without any security compromise (the RNG entropy is still from the browser's native CSPRNG).

#### 4.3.4 Token Refresh

```typescript
keycloak.onTokenExpired = () => {
  keycloak.updateToken(30).catch(() => keycloak.login());
};
```

`updateToken(30)` refreshes the access token if it expires within the next 30 seconds. If refresh fails (e.g., the refresh token itself has expired), it redirects to login. This ensures long-running sessions don't hit 401 errors mid-use.

### 4.4 HTTP Client (`lib/utils/fetch.utils.ts` and `auth-fetch.utils.ts`)

#### 4.4.1 `auth-fetch.utils.ts`

```typescript
export async function authFetch(input, init?): Promise<Response> {
  const token = await getValidToken();
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(input, { ...init, headers });
  if (response.status === 401) getKeycloak().login(); // Auto re-login on 401
  return response;
}
```

`getValidToken()` calls `keycloak.updateToken(30)` before returning the token, ensuring the token is always fresh when making requests. All API calls flow through `authFetch`, so every request automatically carries the current Bearer token.

#### 4.4.2 `fetch.utils.ts` — Typed HTTP Client

The `request<TResponse, TBody>` function is a generic typed wrapper that:

1. **Automatically sets headers:** `Content-Type: application/json` for JSON bodies, skips for `FormData`/`URLSearchParams`/`Blob`/`string` (lets browser set the multipart boundary)
2. **Reads response body appropriately:** JSON, blob, or text based on the `responseFormat` option or `Content-Type` header
3. **Throws `ApiError` on non-2xx:** `ApiError` carries `status` (HTTP code) and `body` (parsed error, often `{ detail: "..." }` from FastAPI)

```typescript
export class ApiError<TBody = unknown> extends Error {
  readonly status: number;
  readonly body: TBody;
  // Constructor extracts human-readable message from FastAPI's { detail: "..." }
}

// Thin helpers consumed by useSWR / useSWRMutation
export const get = <T>(url: string) => request<T>("GET", url);
export const post = <T, B>(url: string, body?: B) =>
  request<T, B>("POST", url, body);
export const del = <T>(url: string) => request<T>("DELETE", url);
```

**SWR integration:** `useSWR` expects a fetcher that takes the key (URL) and returns a promise. `get<T>` has exactly this signature: `(url: string) => Promise<T>`. SWR mutation variants use `useSWRMutation` which expects `(url, { arg }) => Promise<T>`, so the call sites wrap the arg:

```typescript
const { trigger: triggerTest } = useSWRMutation(
  postDatasourceTest,
  (url, { arg }: { arg: DatasourcePayload }) =>
    post<TestConnectionResult, DatasourcePayload>(url, arg),
);
```

### 4.5 URL Configuration (`config/url.config.ts`)

All API endpoint URLs and Keycloak configuration are defined in one place:

```typescript
export const getDatasources = "/api/v1/datasources";
export const postDatasource = "/api/v1/datasources";
export const postDatasourceTest = "/api/v1/datasources/test";
export const postDatasourceUpload = "/api/v1/datasources/upload";
export const postDatasourceRetest = (id: string) =>
  `/api/v1/datasources/${id}/test`;
export const getDatasourceTables = (id, schema, offset, limit) =>
  `/api/v1/datasources/${id}/tables?schema_name=${schema}&offset=${offset}&limit=${limit}`;
export const getDatasourceSearch = (id, schema, query) =>
  `/api/v1/datasources/${id}/search?schema_name=${schema}&query=${query}`;
export const deleteDatasourceUrl = (id: string) => `/api/v1/datasources/${id}`;

// Keycloak
export const keycloakURL = process.env.NEXT_PUBLIC_KEYCLOAK_URL ?? "";
export const keycloakRealm =
  process.env.NEXT_PUBLIC_KEYCLOAK_REALM ?? "insightx";
export const keycloakClientId =
  process.env.NEXT_PUBLIC_KEYCLOAK_CLIENT_ID ?? "insightx-frontend";
```

Centralising URLs prevents hard-coded strings scattered across components and makes URL changes (e.g., adding an API version prefix) a single-line edit.

---

## 5. US 107147 — Database Connector Registration

### User Story

> As a **platform user**, I want to register a new OLAP data source by selecting a database type and providing connection details so that InsightX can query it for analytics.

### Acceptance Criteria (from specification)

- User can select one of 3 supported engines: Oracle 12c+, PostgreSQL, MS SQL Server
- Form captures: Host, Port, Database/Service Name, Username, and engine-specific optional fields
- Each engine has its own field validation rules
- Submitted connection is persisted with a unique name and visible in the data sources list
- Engine icon and type label displayed on the saved connection card

---

### 5.1 Backend Implementation (US 107147)

#### 5.1.1 Registration Endpoint

**File:** `api/app/modules/datasources/router.py`

```python
@router.post("/", response_model=DatasourceResponse, status_code=201)
async def create_datasource(
    payload:      DatasourcePayload,   # Fully validated by Pydantic
    current_user: CurrentUser,          # Auth stub → full Keycloak in M10
    db:           DB,                   # Injected AsyncSession
) -> DatasourceResponse:
    try:
        result = await service.create_datasource(
            payload   = payload,
            tenant_id = current_user["tenant_id"],
            user_id   = current_user["id"],
            db        = db,
        )
        return DatasourceResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
```

`ValueError` from `service.create_datasource` signals a duplicate name (UNIQUE constraint would be violated). The router maps this to HTTP 409 Conflict.

#### 5.1.2 Service: Create Datasource

**File:** `api/app/modules/datasources/service.py`

```python
async def create_datasource(payload, tenant_id, user_id, db) -> dict:
    # 1. Duplicate name check
    existing = await db.execute(
        select(Datasource).where(
            Datasource.tenant_id == tenant_id,
            Datasource.name      == payload.name,
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError(f"A data source named '{payload.name}' already exists.")

    # 2. Encrypt credentials (ONLY place encryption happens for new datasources)
    encrypted_creds = encrypt(payload.credentials.model_dump())

    # 3. Build ORM model
    tls = payload.tls
    datasource = Datasource(
        id                     = uuid.uuid4(),
        name                   = payload.name,
        tenant_id              = tenant_id,
        engine                 = payload.engine.value,
        host                   = payload.host,
        port                   = payload.port,
        database_name          = payload.database,
        oracle_connection_type = payload.oracle_connection_type.value if payload.oracle_connection_type else None,
        auth_method            = payload.auth_method.value,
        encrypted_credentials  = encrypted_creds,
        default_schema         = payload.default_schema or None,
        tls_enabled            = tls.enabled            if tls else False,
        tls_verify_server_cert = tls.verify_server_cert if tls else True,
        tls_mode               = tls.mode               if tls else None,
        tls_ca_cert_path       = tls.ca_cert_path       if tls else None,
        tls_client_cert_path   = tls.client_cert_path   if tls else None,
        tls_client_key_path    = tls.client_key_path    if tls else None,
        created_by             = user_id,
    )

    # 4. Persist (commit happens in get_db())
    db.add(datasource)
    await db.flush()

    # 5. Return with sensitive fields stripped
    return _mask_sensitive_fields(datasource)
```

#### 5.1.3 List Datasources Endpoint

```python
@router.get("/", response_model=DatasourceListResponse)
async def list_datasources(current_user, db) -> DatasourceListResponse:
    sources = await service.list_datasources(tenant_id=current_user["tenant_id"], db=db)
    return DatasourceListResponse(
        data  = [DatasourceResponse(**s) for s in sources],
        count = len(sources),
    )
```

The list is ordered by `created_at.desc()` — most recently added datasources appear first.

#### 5.1.4 Delete Endpoint

```python
@router.delete("/{datasource_id}", status_code=204)
async def delete_datasource(datasource_id, current_user, db) -> None:
    await service.delete_datasource(
        datasource_id = datasource_id,
        tenant_id     = current_user["tenant_id"],
        db            = db,
    )
```

The `DELETE /{id}` endpoint was added in v2. The service fetches the record (enforcing tenant isolation — a tenant can only delete their own datasources), then calls `db.delete(ds)` and `db.flush()`. HTTP 204 (No Content) is returned on success. HTTP 404 is returned if the datasource doesn't exist or belongs to a different tenant.

#### 5.1.5 Engine Configuration

**File:** `api/app/core/engines_config.py`

At the backend, engine configuration only tracks supported auth methods. Everything else (labels, ports, TLS modes) is a UI concern handled by the frontend's `engines.ts`.

#### 5.1.6 Database Schema

The `datasources` table captures everything needed to reconstruct a connection:

```sql
-- Connection identification
name          VARCHAR(100)  -- Human-readable, unique per tenant
engine        VARCHAR(20)   -- "postgresql" | "oracle" | "mssql"

-- Connection details (plaintext — not sensitive)
host          VARCHAR(255)
port          INTEGER
database_name VARCHAR(255)  -- DB name, SID, or Service Name depending on engine

-- Oracle-only
oracle_connection_type VARCHAR(20)  -- "sid" | "service_name" | NULL

-- Schema scope for object browser (new in v2)
default_schema VARCHAR(255)

-- Audit
created_at, updated_at, created_by, last_tested_at, last_test_status
```

---

### 5.2 Frontend Implementation (US 107147)

#### 5.2.1 Overall Page Structure (`app/datasource/page.tsx`)

The datasource page is implemented as a single-file client component that contains three logical sections:

```
DataSourcePage (default export)
├── "Add a connection" section — grid of engine cards (DB_TYPES from dummy-data)
├── "Connected sources" section — list of saved DatasourceRecord cards
└── CredentialModal — inline when credDb !== null

CredentialModal (sub-component)
├── Header — engine logo + name
├── Body — scrollable form (connection → auth → TLS fields)
└── Footer — "Test connection" + "Connect" buttons

TableBrowserView (sub-component)
├── Header — back button + datasource name + status + re-test + sync
├── Search bar
├── Card grid — one TableCard per table/view
└── Pagination controls
```

The page is shown either in **list mode** (`selectedSource === null`) or **browser mode** (`selectedSource !== null`).

#### 5.2.2 Engine Selection and Card Display

Engines are defined in `lib/dummy-data.ts` as `DB_TYPES` for the "Add a connection" tiles. The `TO_ENGINE` map bridges the UI's `db.id` string (e.g., "oracle", "postgres", "sqlserver") to the API's `EngineType` string (e.g., "oracle", "postgresql", "mssql"):

```typescript
const TO_ENGINE: Record<string, EngineType> = {
  oracle: "oracle",
  postgres: "postgresql",
  sqlserver: "mssql",
};

const ENGINE_DISPLAY: Record<EngineType, { slug; letter; color; label }> = {
  oracle: {
    slug: "oracle",
    letter: "O",
    color: "oklch(0.6 0.17 28)",
    label: "Oracle DB",
  },
  postgresql: {
    slug: "postgres",
    letter: "P",
    color: "oklch(0.55 0.13 250)",
    label: "PostgreSQL",
  },
  mssql: {
    slug: "sqlserver",
    letter: "S",
    color: "oklch(0.58 0.15 18)",
    label: "SQL Server",
  },
};
```

Clicking an engine card sets `credDb` state, which renders the `CredentialModal`.

#### 5.2.3 Engine Metadata (`config/engines.ts`)

```typescript
export const ENGINES = {
  postgresql: {
    authMethods: [
      { value: "password", label: "Username & Password" },
      { value: "ldap", label: "LDAP" },
    ],
    tls: {
      defaultMode: "require",
      modes: [
        { value: "require", label: "Require (recommended)" },
        { value: "verify-ca", label: "Verify CA" },
        { value: "verify-full", label: "Verify Full (strictest)" },
      ],
    },
  },
  oracle: {
    authMethods: [
      { value: "password", label: "Username & Password" },
      { value: "wallet", label: "Oracle Wallet" },
      { value: "kerberos", label: "Kerberos" },
    ],
    tls: {
      defaultMode: "ssl",
      modes: [{ value: "ssl", label: "SSL" }],
    },
  },
  mssql: {
    authMethods: [
      { value: "password", label: "Username & Password" },
      { value: "windows", label: "Windows Integrated Auth" },
      { value: "azure_ad", label: "Azure Active Directory" },
    ],
    tls: {
      defaultMode: "encrypt",
      modes: [{ value: "encrypt", label: "Encrypt" }],
    },
  },
};
```

`CredentialModal` reads `ENGINES[engineId]` to determine which auth methods to show as buttons and which TLS modes to list in the dropdown. Both are driven from this single config.

#### 5.2.4 `CredentialModal`: Connection Fields

The modal opens with all connection fields at the top of the body:

```typescript
// ── Connection name
<input value={name} onChange={(e) => { setName(e.target.value); resetTest(); }} />

// ── Host + Port (row layout)
<input value={host} />
<input value={port} />   // ← Pre-filled to db.port from the engine card

// ── Oracle connection type selector (Oracle only)
{engineId === 'oracle' && (
  <select value={oracleConnType} onChange={...}>
    <option value="service_name">Service name</option>
    <option value="sid">SID</option>
  </select>
)}

// ── Database / SID / Service Name
<input value={database} placeholder={engineId === 'oracle' ? 'COREPDB' : 'analytics'} />

// ── Schema (required — new in v2)
<input value={defaultSchema} placeholder={engineId === 'oracle' ? 'ANALYTICS_OWNER' : 'public'} />
```

The Schema field is **required** (blocks the "Test connection" button if empty via `canTest`). It specifies which schema/owner the object browser will scope to. This replaces the schema-selection step that would otherwise need to happen after connecting.

#### 5.2.5 `canTest` Gate

```typescript
function credentialsReady(): boolean {
  if (authMethod === "password" || authMethod === "ldap")
    return !!(authCredentials.username && authCredentials.password);
  if (authMethod === "wallet")
    return authCredentials.walletFile instanceof File;
  if (authMethod === "kerberos")
    return (
      authCredentials.keytabFile instanceof File && !!authCredentials.principal
    );
  if (authMethod === "windows") return true; // No required fields
  if (authMethod === "azure_ad") return !!authCredentials.access_token;
  return false;
}

const canTest = !!(
  name.trim() &&
  host.trim() &&
  database.trim() &&
  defaultSchema.trim() && // ← New in v2
  credentialsReady()
);
```

The "Test connection" button's `disabled` state is driven entirely by `canTest`. Users can't attempt a test until all required fields are filled, which prevents confusing "field required" errors from the API.

#### 5.2.6 Connected Source List Display

After fetching the datasource list via `useSWR`, the list is rendered as rows:

```typescript
const {
  data: datasources = [],
  isLoading,
  mutate: mutateDatasources, // ← SWR's cache mutator for optimistic updates
} = useSWR<DatasourceRecord[]>(
  getDatasources,
  (url) => get<DatasourceListResponse>(url).then((r) => r.data), // ← Unwrap { data, count }
);
```

Each row (`connected-row`) shows:

- `DBLogo` badge (engine logo image or fallback letter avatar)
- Name + `host:port/database_name` + schema name
- Engine label + last tested date
- Status pill (green "Connected" / red "Failed" / grey "Not tested")
- Delete button (`X`) with hover danger styling
- Chevron-right hint (click → opens TableBrowserView)

The delete button is the only action that doesn't require entering the browser view:

```typescript
async function handleDelete(e, source) {
  e.stopPropagation();   // ← Prevent row click from opening browser
  if (!window.confirm(`Remove "${source.name}"?`)) return;
  setDeletingId(source.id);
  try {
    await triggerDelete(source.id);
    mutateDatasources(
      (prev) => prev.filter((s) => s.id !== source.id),
      { revalidate: false }   // ← Optimistic: don't re-fetch, just update cache
    );
  } catch { ... }
}
```

#### 5.2.7 SWR Cache Mutations (Optimistic Updates)

SWR's `mutate` function allows updating the cache immediately without waiting for a refetch:

```typescript
// After delete: remove from local cache
mutateDatasources((prev) => prev.filter((s) => s.id !== source.id), {
  revalidate: false,
});

// After retest: update status in local cache
mutateDatasources(
  (prev) => prev.map((s) => (s.id === updated.id ? updated : s)),
  { revalidate: false },
);

// After create: full refetch (ensures fresh data from server)
mutateDatasources();
```

This keeps the UI responsive — operations appear instant even before the server responds.

#### 5.2.8 `DBLogo` Component

```typescript
const LOGOS: Record<string, string> = {
  oracle:     "https://cdn.jsdelivr.net/.../oracle-original.svg",
  postgres:   "https://cdn.jsdelivr.net/.../postgresql-original.svg",
  postgresql: "https://cdn.jsdelivr.net/.../postgresql-original.svg",
  sqlserver:  "https://cdn.jsdelivr.net/.../microsoftsqlserver-plain.svg",
};

export default function DBLogo({ slug, size, radius, letter, color }) {
  const [err, setErr] = useState(false);
  const src = LOGOS[slug];

  if (err || !src) {
    // Fallback: solid-colour circle with initials
    return (
      <div style={{ background: color, borderRadius: radius, ... }}>
        {letter ?? "?"}
      </div>
    );
  }
  return (
    <div className="db-tile" style={{ width: size, height: size, borderRadius: radius }}>
      <img src={src} onError={() => setErr(true)} />
    </div>
  );
}
```

The fallback letter avatar ensures the UI never shows broken images if CDN resources are unavailable (e.g., air-gapped enterprise environments).

---

## 6. US 107148 — Authentication Configuration

### User Story

> As a **platform user**, I want to choose the appropriate authentication method for my database connection so that InsightX can authenticate securely according to my organisation's security policies.

### Acceptance Criteria (from specification)

- Oracle: Username/Password, Oracle Wallet (.sso/.p12), Kerberos (keytab + principal)
- PostgreSQL: Username/Password (SCRAM-SHA-256), LDAP/AD pass-through
- MSSQL: SQL Auth, Windows Integrated Auth, Azure AD (OAuth2 token)
- Auth method selector shown contextually based on selected engine
- Credentials encrypted at rest using AES-256

---

### 6.1 Backend Implementation (US 107148)

#### 6.1.1 Credential Encryption (`credential_encryptor.py`)

```
ENCRYPTION ALGORITHM: AES-256-GCM
KEY SIZE: 256 bits (32 bytes = 64 hex chars from CREDENTIAL_ENCRYPTION_KEY)
NONCE SIZE: 96 bits (12 bytes) — NIST recommended for GCM
TAG SIZE: 128 bits (16 bytes) — maximum GCM authentication strength

STORAGE FORMAT: "iv_hex:tag_hex:ciphertext_hex"

Example: "a3f1b2c0...(24 hex chars):d4e5f6...(32 hex chars):8a9b0c...(variable)"
```

```python
def encrypt(credentials: dict) -> str:
    key = _get_key()           # 32 bytes from hex env var
    iv  = os.urandom(12)       # Cryptographically random nonce — NEVER reuse

    aesgcm         = AESGCM(key)
    plaintext      = json.dumps(credentials).encode("utf-8")

    # AESGCM.encrypt() returns: ciphertext_bytes + 16-byte_auth_tag (concatenated)
    ciphertext_and_tag = aesgcm.encrypt(iv, plaintext, None)

    ciphertext = ciphertext_and_tag[:-16]   # All but last 16 bytes
    tag        = ciphertext_and_tag[-16:]   # Last 16 bytes

    return ":".join([iv.hex(), tag.hex(), ciphertext.hex()])
```

**Why GCM?** GCM (Galois/Counter Mode) provides both **encryption** (confidentiality) and **authentication** (integrity). The 128-bit tag means any modification to the stored ciphertext — whether accidental corruption or deliberate tampering — causes decryption to fail with `InvalidTag`. This prevents credential substitution attacks where an attacker with DB write access might swap one encrypted blob for another.

**Why random IV?** A new random IV for each encryption means encrypting the same credentials twice produces different ciphertext. This prevents an attacker from deducing which datasources share the same credentials by comparing ciphertext values.

```python
def decrypt(encrypted_string: str) -> dict:
    parts = encrypted_string.split(":")
    if len(parts) != 3:
        raise ValueError("Malformed encrypted credential")

    iv, tag, ciphertext = [bytes.fromhex(p) for p in parts]

    key    = _get_key()
    aesgcm = AESGCM(key)

    # Verifies auth tag AND decrypts in one operation
    # Raises cryptography.exceptions.InvalidTag if tag doesn't match
    plaintext = aesgcm.decrypt(iv, ciphertext + tag, None)

    return json.loads(plaintext.decode("utf-8"))
```

**Key validation:**

```python
def _get_key() -> bytes:
    hex_key = settings.credential_encryption_key
    if not hex_key or len(hex_key) != 64:
        raise ValueError(
            "CREDENTIAL_ENCRYPTION_KEY must be a 64-character hex string."
        )
    return bytes.fromhex(hex_key)
```

The key check happens on every encrypt/decrypt call, which ensures a missing or malformed key is detected immediately rather than silently using a weakened key.

#### 6.1.2 Connection Tester — Error Classification (`connection_tester.py`)

The connection tester dispatches to per-engine drivers and classifies raw exceptions into user-friendly categories:

```python
async def test_connection(config: dict) -> dict:
    driver_fn = {"postgresql": test_postgres_connection,
                 "mssql":      test_mssql_connection,
                 "oracle":     test_oracle_connection}.get(config.get("engine"))

    if driver_fn is None:
        return {"success": False, "category": "UNSUPPORTED_ENGINE", ...}

    try:
        result = await asyncio.wait_for(driver_fn(config), timeout=10.0)
    except asyncio.TimeoutError:
        return {"success": False, "category": "TIMEOUT", ...}

    if result["success"]:
        return {"success": True, "latency_ms": result["latency_ms"]}

    # Classify the raw exception
    raw_error  = result.get("raw_error") or Exception("Unknown error")
    classified = _classify_error(config.get("engine", ""), raw_error)
    return {"success": False, "latency_ms": result["latency_ms"], **classified}
```

`_classify_error` performs keyword matching against the error string:

```python
def _classify_error(engine: str, error: Exception) -> dict:
    msg = str(error).lower()

    # Authentication failures (engine-specific ORA codes included)
    if any(p in msg for p in [
        "password authentication failed",   # asyncpg
        "invalid password",                 # asyncpg InvalidPasswordError
        "login failed",                     # pyodbc MSSQL
        "invalid username/password",        # python-oracledb
        "ora-01017",                        # Oracle: invalid username/password
        "authentication failed",
        "invalid token",                    # Azure AD
    ]):
        return {"category": "AUTH_FAILED", "message": "..."}

    # Host unreachable
    if any(p in msg for p in [
        "connection refused",
        "tns:no listener",       # Oracle
        "ora-12541",             # Oracle: no listener
        "[08001]",               # SQLSTATE
        "getaddrinfo failed",    # Windows DNS
    ]):
        return {"category": "HOST_UNREACHABLE", "message": "..."}

    # TLS failures
    if any(p in msg for p in ["ssl", "tls", "certificate", "handshake", "ora-29024"]):
        return {"category": "TLS_HANDSHAKE_FAILED", "message": "..."}

    # Timeout
    if any(p in msg for p in ["timeout", "timed out", "ora-12170"]):
        return {"category": "TIMEOUT", "message": "..."}

    # Unsupported config
    if any(p in msg for p in [
        "not supported in thin mode",   # Kerberos needs Thick Mode
        "no microsoft odbc driver",     # MSSQL without ODBC
    ]):
        return {"category": "UNSUPPORTED_CONFIG", "message": "..."}

    return {"category": "UNKNOWN", "message": f"Connection failed: {str(error)}"}
```

Both error classification (keyword matching) and error messages are engine-aware (Oracle ORA codes, MSSQL SQLSTATE codes, asyncpg-specific strings).

#### 6.1.3 PostgreSQL Driver (`drivers/postgres_driver.py`)

```python
async def test_postgres_connection(config: dict) -> dict:
    credentials = config["credentials"]
    tls         = config.get("tls") or {}
    ssl_context = _build_ssl_context(tls)

    conn = await asyncpg.connect(
        host     = config["host"],
        port     = int(config["port"]),
        database = config["database"],
        user     = credentials["username"],
        password = credentials["password"],
        ssl      = ssl_context,
        timeout  = 10.0,
    )
    await conn.fetchval("SELECT 1")
    # ... measure latency, close conn, return result
```

`credentials["username"]` and `credentials["password"]` are plain dict lookups — the dict was produced by `credential_encryptor.decrypt()` for re-tests, or comes from the validated Pydantic model's `.model_dump()` for pre-save tests.

For LDAP auth, the connection string is identical to password auth. PostgreSQL handles LDAP forwarding via `pg_hba.conf` — the driver just sends the username/password and the server determines authentication method.

#### 6.1.4 Oracle Driver (`drivers/oracle_driver.py`)

```python
async def test_oracle_connection(config: dict) -> dict:
    auth_method    = config["auth_method"]
    credentials    = config["credentials"]
    connect_string = _build_connect_string(config)

    if auth_method == "password":
        connection = await oracledb.connect_async(
            user     = credentials["username"],
            password = credentials["password"],
            dsn      = connect_string,
            tcp_connect_timeout = 10,
        )

    elif auth_method == "wallet":
        connection = await oracledb.connect_async(
            dsn                 = connect_string,
            wallet_location     = credentials["wallet_location"],  # ← Server-side dir
            wallet_password     = credentials.get("wallet_password"),
            user                = credentials.get("username") or None,
            tcp_connect_timeout = 10,
        )

    elif auth_method == "kerberos":
        connection = await oracledb.connect_async(
            user               = f"/{credentials['principal']}",   # ← "/" prefix = external auth
            dsn                = connect_string,
            externalauth       = True,
            tcp_connect_timeout = 10,
        )
```

**Oracle wallet connection:** `wallet_location` is the **directory** containing the wallet files (not the file itself). Oracle's `connect_async` reads `cwallet.sso` (auto-login) or `ewallet.p12` (password-protected) from that directory. `wallet_password` is only needed for `.p12` format.

**Oracle Kerberos connection:** Uses `user="/{principal}"` with `externalauth=True`. The leading `/` tells Oracle to use OS authentication. The Kerberos TGT must already be obtained by the OS (via the keytab file). This requires Oracle Thick Mode (Instant Client installed server-side) — the `_classify_error` function specifically handles "not supported in thin mode" errors from this.

**`_build_connect_string`** produces three formats depending on connection type and TLS:

```python
def _build_connect_string(config: dict) -> str:
    host, port, database = config["host"], config["port"], config["database"]
    conn_type = config.get("oracle_connection_type", "service_name")
    tls = config.get("tls") or {}

    if tls.get("enabled"):
        # TCPS: Oracle SSL. SSL_SERVER_DN_MATCH controls cert hostname verification.
        dn_match = "YES" if tls.get("verify_server_cert", True) else "NO"
        return (
            f"(DESCRIPTION="
            f"(ADDRESS=(PROTOCOL=TCPS)(HOST={host})(PORT={port}))"
            f"(CONNECT_DATA=(SERVICE_NAME={database}))"
            f"(SECURITY=(SSL_SERVER_DN_MATCH={dn_match}))"
            f")"
        )

    if conn_type == "sid":
        return (  # Legacy SID format (Oracle 11g era)
            f"(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST={host})(PORT={port}))"
            f"(CONNECT_DATA=(SID={database})))"
        )

    return f"{host}:{port}/{database}"  # Easy Connect (Service Name)
```

#### 6.1.5 MSSQL Driver (`drivers/mssql_driver.py`)

MSSQL uses `pyodbc`, which is synchronous. Since FastAPI runs on asyncio, the synchronous driver is wrapped in `asyncio.to_thread()`:

```python
async def test_mssql_connection(config: dict) -> dict:
    return await asyncio.to_thread(_sync_test_mssql, config)
```

`asyncio.to_thread()` runs the blocking function in a thread pool executor, yielding the event loop while MSSQL is connecting. This prevents a slow MSSQL connection from blocking all other requests.

**ODBC Driver detection:**

```python
def _get_odbc_driver() -> str:
    available = pyodbc.drivers()
    for preferred in [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 13 for SQL Server",
    ]:
        if preferred in available:
            return preferred
    raise RuntimeError("No Microsoft ODBC Driver for SQL Server found...")
```

The driver preference order matches Microsoft's recommendation (prefer newer). If none is installed, the error propagates through `_classify_error` as `UNSUPPORTED_CONFIG`.

**Azure AD token auth:**

```python
if auth_method == "azure_ad":
    SQL_COPT_SS_ACCESS_TOKEN = 1256
    token        = credentials["access_token"].encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token)}s", len(token), token)
    conn = pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})
```

This follows Microsoft's specification for passing Azure AD access tokens to the ODBC driver. The token must be encoded as UTF-16-LE with a little-endian 4-byte length prefix, then passed via the `SQL_COPT_SS_ACCESS_TOKEN` connection attribute.

---

### 6.2 Frontend Implementation (US 107148)

#### 6.2.1 Auth Method Selection (Tab Buttons)

In `CredentialModal`, auth methods are rendered as toggle buttons filtered by engine:

```typescript
const availableAuthMethods = engineConfig.authMethods;  // From ENGINES[engineId]

{availableAuthMethods.length > 1 && (
  <div className="field">
    <label>Authentication method</label>
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      {availableAuthMethods.map((m) => (
        <button
          key={m.value}
          className={`btn btn-sm ${authMethod === m.value ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => {
            setAuthMethod(m.value);
            setAuthCredentials({});  // ← Clear credentials when method changes
            resetTest();
          }}
        >
          {m.label}
        </button>
      ))}
    </div>
  </div>
)}
```

Clearing `authCredentials` when switching methods prevents credential leakage (e.g., a wallet file object staying in state when the user switches to password auth).

#### 6.2.2 Credential Sub-Forms

Each auth method renders a specific set of fields within the modal body, using conditional rendering:

**Password / LDAP:**

```typescript
{(authMethod === 'password' || authMethod === 'ldap') && (
  <div className="row">
    <input placeholder="readonly_svc" ... />   {/* Username */}
    <input type="password" ... />              {/* Password */}
  </div>
)}
```

**Oracle Wallet:**

```typescript
{authMethod === 'wallet' && (
  <>
    <div style={infoBoxStyle}>
      💡 Upload your Oracle Wallet file (.sso or .p12)...
    </div>
    <input
      type="file"
      accept=".sso,.p12"
      onChange={(e) => {
        const file = e.target.files?.[0];
        if (file) {
          setAuthCredentials((p) => ({
            ...p,
            walletFile:     file,        // ← File object stored in state
            walletFileName: file.name,   // ← Displayed to user
          }));
          resetTest();
        }
      }}
    />
    {/* Optional wallet password */}
    <input type="password" ... />
  </>
)}
```

**Kerberos:**

```typescript
{authMethod === 'kerberos' && (
  <>
    <input type="file" onChange={...} />     {/* keytabFile stored */}
    <input className="mono" placeholder="user@REALM.COM" ... />  {/* principal */}
  </>
)}
```

**Windows Auth (NTLM):**

```typescript
{authMethod === 'windows' && (
  <>
    <div style={infoBoxStyle}>💡 Uses NTLM — only on Windows hosts in same AD domain.</div>
    <input placeholder="CORP" ... />   {/* Optional domain */}
  </>
)}
```

**Azure AD:**

```typescript
{authMethod === 'azure_ad' && (
  <textarea
    rows={4}
    placeholder="Paste your Azure AD access token here"
    className="input mono"
    value={(authCredentials.access_token as string) ?? ''}
    onChange={(e) => setAuthCredentials((p) => ({ ...p, access_token: e.target.value }))}
  />
)}
```

#### 6.2.3 File Upload Resolution (`resolveUploads`)

Before calling the test or create API, `File` objects in state must be uploaded and replaced with server paths:

```typescript
async function resolveUploads(): Promise<{
  credentials: Record<string, unknown>;
  tlsExtra: Record<string, string | null>;
}> {
  const creds: Record<string, unknown> = { ...authCredentials };
  const tlsExtra: Record<string, string | null> = {};

  // Upload Oracle Wallet file
  if (creds.walletFile instanceof File) {
    const res = await triggerUpload({ file: creds.walletFile, type: "wallet" });
    creds.walletLocation = res?.path; // ← Server path replaces File object
    delete creds.walletFile; // ← Remove non-serialisable File
    delete creds.walletFileName; // ← Remove display-only field
  }

  // Upload Kerberos keytab
  if (creds.keytabFile instanceof File) {
    const res = await triggerUpload({ file: creds.keytabFile, type: "keytab" });
    creds.keytabPath = res?.path;
    delete creds.keytabFile;
    delete creds.keytabFileName;
  }

  // Upload TLS certs
  if (caCertFile) {
    const res = await triggerUpload({ file: caCertFile, type: "ca_cert" });
    tlsExtra.ca_cert_path = res?.path ?? null;
  }
  // ... client_cert, client_key

  return { credentials: creds, tlsExtra };
}
```

The result of `resolveUploads()` is stored in React state:

```typescript
const [resolvedCredentials, setResolvedCredentials] = useState<Record<
  string,
  unknown
> | null>(null);
const [resolvedTlsExtra, setResolvedTlsExtra] = useState<
  Record<string, string | null>
>({});
```

When the user clicks "Connect" (save), the stored `resolvedCredentials` and `resolvedTlsExtra` are used rather than calling `resolveUploads()` again (which would try to re-upload already-uploaded files and fail since the `File` objects were deleted from state).

#### 6.2.4 `buildPayload` Function

```typescript
function buildPayload(
  credentials: Record<string, unknown> = authCredentials,
  tlsExtra: Record<string, string | null> = {},
): DatasourcePayload {
  return {
    name,
    engine: engineId,
    host,
    port: parseInt(String(port), 10),
    database,
    oracle_connection_type: engineId === "oracle" ? oracleConnType : undefined,
    default_schema: defaultSchema.trim(),
    auth_method: authMethod as AuthMethod,
    credentials,
    tls: {
      enabled: useTls,
      mode: tlsMode,
      verify_server_cert: tlsVerify,
      ca_cert_path: tlsExtra.ca_cert_path ?? null,
      client_cert_path: tlsExtra.client_cert_path ?? null,
      client_key_path: tlsExtra.client_key_path ?? null,
    },
  };
}
```

#### 6.2.5 Test → Save Flow

```typescript
async function handleTest() {
  if (!canTest) return;
  setTesting(true);
  resetTest();
  try {
    const { credentials, tlsExtra } = await resolveUploads();
    const result = await triggerTest(buildPayload(credentials, tlsExtra));
    if (!result) return;
    setResolvedCredentials(credentials);   // ← Store for save
    setResolvedTlsExtra(tlsExtra);         // ← Store for save
    setTestResult({ success: result.success, latency_ms: result.latency_ms, ... });
  } catch (err) {
    setTestResult({ success: false, message: err.message });
  } finally {
    setTesting(false);
  }
}

async function handleSave() {
  if (!testResult?.success) return;   // ← Gated: only after successful test
  setSaving(true);
  try {
    const creds = resolvedCredentials ?? authCredentials;
    const tlsExtra = resolvedTlsExtra ?? {};
    await triggerCreate(buildPayload(creds, tlsExtra));   // ← Reuse stored resolved state
    onCreated();   // ← Triggers parent to mutateDatasources() (re-fetch)
    onClose();
  } catch (err) {
    setSaveError(err.message);
  } finally {
    setSaving(false);
  }
}
```

**Key flow:** `resolveUploads()` is called during `handleTest()`, not `handleSave()`. This prevents:

1. Double-uploading file content
2. `File` objects becoming unavailable (browsers can revoke file references)
3. The save action failing because files no longer exist in state

**Bug prevention:** `resetTest()` is called on every form field change. This clears `resolvedCredentials` and `resolvedTlsExtra`, forcing a fresh `resolveUploads()` call if the user modifies any field after testing.

---

## 7. US 107149 — TLS/SSL Encryption Configuration

### What is TLS/SSL?

**TLS (Transport Layer Security)** is a cryptographic protocol that provides three guarantees for network communication:

1. **Confidentiality:** Data is encrypted in transit using symmetric encryption (AES-256 in modern TLS). Anyone intercepting the network packets sees only encrypted bytes.

2. **Authentication:** The server proves its identity using a digital certificate (X.509). The client verifies the certificate is signed by a trusted Certificate Authority (CA) and that the certificate's hostname matches the server being connected to.

3. **Integrity:** The MAC (Message Authentication Code) built into TLS ensures data cannot be modified in transit without detection.

**SSL** (Secure Sockets Layer) is the predecessor to TLS, last standardised as SSL 3.0 in 1996. SSL is deprecated and broken; "SSL" in database connection terminology almost always means TLS 1.2 or 1.3 in practice. The InsightX UI uses "SSL/TLS" together to accommodate database documentation that uses "SSL" loosely.

**TLS Handshake (simplified):**

```
Client                              Server
  │                                   │
  │──── ClientHello ─────────────────→│  TLS version, cipher suites, random
  │←─── ServerHello ─────────────────│  Chosen cipher, session ID
  │←─── Certificate ─────────────────│  Server's X.509 cert (public key)
  │←─── ServerHelloDone ─────────────│
  │                                   │
  │  [Client verifies certificate]    │
  │                                   │
  │──── ClientKeyExchange ───────────→│  Pre-master secret (encrypted with server pubkey)
  │──── ChangeCipherSpec ────────────→│  "Switching to encrypted mode"
  │──── Finished ────────────────────→│  Verification hash
  │                                   │
  │←─── ChangeCipherSpec ────────────│
  │←─── Finished ────────────────────│
  │                                   │
  [Encrypted application data begins]
```

**Certificate verification levels:**

| Level                       | What's verified                                 | Use case                                        |
| --------------------------- | ----------------------------------------------- | ----------------------------------------------- |
| No TLS                      | Nothing (plaintext)                             | Dev only, never production                      |
| Require / Encrypt           | Server uses TLS but cert not checked            | Basic encryption without trust                  |
| Verify CA (`verify-ca`)     | Cert signed by trusted CA, hostname not checked | Production with wildcard or internal certs      |
| Verify Full (`verify-full`) | Cert signed by trusted CA AND hostname matches  | Strictest; recommended for internet-exposed DBs |

**Mutual TLS (mTLS):** The client also presents a certificate to the server. The server verifies the client cert. Both sides authenticate each other. Required in some banking and defense environments.

**Self-signed certificates:** Generated without a CA. Clients don't trust them by default. Used for testing — the UI shows a security warning when verification is disabled to accommodate self-signed certs.

---

### 7.1 Backend Implementation (US 107149)

#### 7.1.1 TLS Schema

```python
class TLSConfig(BaseModel):
    enabled:            bool = False
    verify_server_cert: bool = True     # Secure by default

    # Engine-aware mode labels
    # PostgreSQL: "require" | "verify-ca" | "verify-full" | "disable"
    # MSSQL: "encrypt"
    # Oracle: "ssl"
    mode: Optional[str] = None

    # Server-side paths from POST /upload — never returned to client
    ca_cert_path:     Optional[str] = None
    client_cert_path: Optional[str] = None
    client_key_path:  Optional[str] = None

    @model_validator(mode="after")
    def validate_tls(self) -> "TLSConfig":
        if self.enabled and not self.mode:
            raise ValueError("tls.mode is required when tls.enabled is true")
        return self
```

The `mode` validation prevents the common misconfiguration of enabling TLS without specifying how certificates should be verified.

#### 7.1.2 PostgreSQL TLS (`_build_ssl_context`)

```python
def _build_ssl_context(tls: dict) -> Optional[ssl.SSLContext]:
    if not tls or not tls.get("enabled"):
        return None

    if tls.get("verify_server_cert", True):
        if tls.get("ca_cert_path"):
            # Custom CA: load the specific PEM file
            ctx = ssl.create_default_context(cafile=tls["ca_cert_path"])
        else:
            # System CA store: trusts publicly trusted CAs (Let's Encrypt, DigiCert, etc.)
            ctx = ssl.create_default_context()
    else:
        # No verification (user opted in; warning shown in UI)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE

    # mTLS: client presents certificate to server
    if tls.get("client_cert_path") and tls.get("client_key_path"):
        ctx.load_cert_chain(
            certfile = tls["client_cert_path"],
            keyfile  = tls["client_key_path"],
        )

    return ctx
```

The `ssl.SSLContext` object is passed directly to `asyncpg.connect(ssl=ssl_context)`. asyncpg uses this context for all TLS negotiation.

The `mode` field (`require`, `verify-ca`, `verify-full`) maps to the `ctx.verify_mode` / `ctx.check_hostname` combination:

| PostgreSQL mode | `check_hostname` | `verify_mode`   |
| --------------- | ---------------- | --------------- |
| `require`       | False            | `CERT_NONE`     |
| `verify-ca`     | False            | `CERT_REQUIRED` |
| `verify-full`   | True             | `CERT_REQUIRED` |

Note: In the current implementation, `require` maps to `CERT_NONE` (encrypted but unverified). A stricter interpretation would be `CERT_OPTIONAL`. For production, `verify-full` with a CA cert is recommended.

#### 7.1.3 Oracle TLS (TCPS in Connect String)

Oracle TLS is configured via the connection descriptor string rather than a Python `ssl.SSLContext`:

```python
if tls.get("enabled"):
    dn_match = "YES" if tls.get("verify_server_cert", True) else "NO"
    return (
        f"(DESCRIPTION="
        f"(ADDRESS=(PROTOCOL=TCPS)(HOST={host})(PORT={port}))"
        f"(CONNECT_DATA=(SERVICE_NAME={database}))"
        f"(SECURITY=(SSL_SERVER_DN_MATCH={dn_match}))"
        f")"
    )
```

- `PROTOCOL=TCPS` tells Oracle to use TLS (TCP Secure)
- `SSL_SERVER_DN_MATCH=YES` verifies the server certificate's Distinguished Name
- Oracle's TLS cert must be placed in a wallet; the wallet location is set via `TNS_ADMIN` or passed directly in the connection string for newer Oracle versions

#### 7.1.4 MSSQL TLS (Connection String Keywords)

```python
if tls.get("enabled"):
    conn_parts.append("Encrypt=yes")
    trust = "no" if tls.get("verify_server_cert", True) else "yes"
    conn_parts.append(f"TrustServerCertificate={trust}")
else:
    conn_parts.append("Encrypt=no")
```

Note: MSSQL's `TrustServerCertificate` is inverted from InsightX's `verify_server_cert`:

- InsightX `verify_server_cert=True` → ODBC `TrustServerCertificate=no` (don't blindly trust)
- InsightX `verify_server_cert=False` → ODBC `TrustServerCertificate=yes` (trust everything)

Azure AD auth always adds `Encrypt=yes` even if TLS was not explicitly enabled, as Azure AD requires encrypted connections:

```python
elif auth_method == "azure_ad":
    if not tls.get("enabled"):
        conn_parts.append("Encrypt=yes")
        conn_parts.append("TrustServerCertificate=no")
```

#### 7.1.5 TLS Storage

TLS configuration is stored as individual columns in the `datasources` table:

```
tls_enabled           BOOLEAN    NOT NULL DEFAULT FALSE
tls_verify_server_cert BOOLEAN   NOT NULL DEFAULT TRUE   -- Secure by default
tls_mode              VARCHAR(20)                         -- Mode label
tls_ca_cert_path      VARCHAR(500)                       -- Server-side file path
tls_client_cert_path  VARCHAR(500)
tls_client_key_path   VARCHAR(500)
```

**File content is NEVER stored in the DB.** Only server-side paths. This means:

- Certificate content doesn't bloat the database
- Certificates can be rotated by uploading a new file and updating the path
- The database remains readable even if cert paths are lost

---

### 7.2 Frontend Implementation (US 107149)

#### 7.2.1 TLS Toggle and Mode Selector

The TLS section appears at the bottom of the `CredentialModal` body:

```typescript
{/* TLS toggle */}
<label style={{ display: 'flex', alignItems: 'center', gap: 9, cursor: 'pointer', ... }}>
  <input
    type="checkbox"
    checked={useTls}
    onChange={(e) => { setUseTls(e.target.checked); resetTest(); }}
  />
  Use SSL / TLS encrypted connection
</label>

{/* TLS options (shown only when TLS is enabled) */}
{useTls && (
  <>
    {/* Mode selector (only when engine has multiple modes) */}
    {tlsModes.length > 1 && (
      <select value={tlsMode} onChange={(e) => { setTlsMode(e.target.value); resetTest(); }}>
        {tlsModes.map((m) => (
          <option key={m.value} value={m.value}>{m.label}</option>
        ))}
      </select>
    )}

    {/* Verify server certificate */}
    <label>
      <input type="checkbox" checked={tlsVerify} onChange={...} />
      Verify server certificate
    </label>

    {/* Security warning when verification is off */}
    {!tlsVerify && (
      <div style={{ color: 'oklch(0.65 0.15 55)', background: 'oklch(0.2 0.04 55)', ... }}>
        ⚠ Skipping cert verification exposes this connection to man-in-the-middle attacks.
        Only use for testing with self-signed certificates.
      </div>
    )}

    {/* CA cert upload (when verify is on) */}
    {tlsVerify && (
      <input type="file" accept=".pem,.crt,.cer" onChange={(e) => setCaCertFile(e.target.files?.[0])} />
    )}

    {/* mTLS section */}
    <input type="file" accept=".pem,.crt,.cer" onChange={...} />  {/* Client cert */}
    <input type="file" accept=".pem,.key"      onChange={...} />  {/* Client key */}
  </>
)}
```

#### 7.2.2 TLS State Management

TLS state is flat component state (not a custom hook):

```typescript
const [useTls, setUseTls] = useState(true); // Default: TLS enabled
const [tlsMode, setTlsMode] = useState(
  engineConfig.tls?.defaultMode ?? DEFAULT_TLS_MODE[engineId] ?? "",
);
const [tlsVerify, setTlsVerify] = useState(true); // Verify by default
const [caCertFile, setCaCertFile] = useState<File | null>(null);
const [clientCertFile, setClientCertFile] = useState<File | null>(null);
const [clientKeyFile, setClientKeyFile] = useState<File | null>(null);
```

Initial `tlsMode` comes from `engineConfig.tls.defaultMode` in `engines.ts`:

- PostgreSQL defaults to `"require"`
- Oracle defaults to `"ssl"`
- MSSQL defaults to `"encrypt"`

These defaults reflect the most common TLS configuration for each engine in enterprise environments.

#### 7.2.3 Engine-Aware TLS Labels

The mode selector label changes per engine (not currently in the code but described in the UI spec):

- PostgreSQL: "SSL Mode"
- MSSQL: "Encrypt" (a checkbox, not a mode selector, since MSSQL's TLS is binary)
- Oracle: "SSL" (single mode)

Currently the UI uses a `<select>` for all engines and hides it when only one mode is available (`tlsModes.length > 1`). This means MSSQL and Oracle users see only the TLS toggle and verify checkbox, not a mode selector.

---

## 8. US 107150 — Connection Test & Validation

### User Story

> As a **platform user**, I want to test my database connection before saving it so that I can confirm whether my credentials, host, port, and TLS settings are correct.

### Acceptance Criteria (from specification)

- "Test Connection" button available before saving
- Test shows one of: ✅ Success, ❌ Auth Failed, ❌ Host Unreachable, ❌ TLS Handshake Failed, ❌ Timeout
- Test is non-destructive (SELECT 1 only)
- Test respects current auth method and TLS settings
- Response time shown (e.g., "Connected in 240ms")
- Save button disabled until successful test
- Timeout threshold is 10 seconds

---

### 8.1 Backend Implementation (US 107150)

#### 8.1.1 Pre-Save Test Endpoint

```python
@router.post("/test", response_model=TestConnectionResponse)
async def test_connection(
    payload:      DatasourcePayload,
    current_user: CurrentUser,
) -> TestConnectionResponse:
    # No DB session — this test is stateless and non-destructive
    result = await service.test_datasource_connection(payload)
    return TestConnectionResponse(**result)
```

No DB session is needed for the pre-save test. The credentials come in plaintext in the request body, are tested immediately, and are never persisted. The `current_user` dependency is still present to enforce authentication (all endpoints require auth), but no tenant-scoped data is accessed.

#### 8.1.2 Re-Test Saved Datasource Endpoint

```python
@router.post("/{datasource_id}/test", response_model=TestConnectionResponse)
async def retest_saved_datasource(
    datasource_id: str,
    current_user:  CurrentUser,
    db:            DB,
) -> TestConnectionResponse:
    try:
        result = await service.retest_saved_datasource(
            datasource_id = datasource_id,
            tenant_id     = current_user["tenant_id"],
            db            = db,
        )
        return TestConnectionResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to decrypt credentials: {exc}",
        )
```

The `Exception` catch handles decryption failures (wrong key, tampered data) with a specific 500 error message. This surfaces immediately if the `CREDENTIAL_ENCRYPTION_KEY` is changed after data was encrypted with an old key.

#### 8.1.3 Service: Retest

```python
async def retest_saved_datasource(datasource_id, tenant_id, db) -> dict:
    ds = await _get_datasource(datasource_id, tenant_id, db)
    config = _datasource_runtime_config(ds)   # Decrypt + reconstruct config

    test_result = await test_connection(config)

    # Update audit fields
    ds.last_tested_at   = datetime.now(timezone.utc)
    ds.last_test_status = "success" if test_result["success"] else "failed"
    await db.flush()

    return test_result
```

`_get_datasource` enforces tenant isolation:

```python
async def _get_datasource(datasource_id, tenant_id, db) -> Datasource:
    result = await db.execute(
        select(Datasource).where(
            Datasource.id        == uuid.UUID(datasource_id),
            Datasource.tenant_id == tenant_id,          # ← Tenant check
        )
    )
    ds = result.scalar_one_or_none()
    if ds is None:
        raise ValueError(f"Data source '{datasource_id}' not found.")
    return ds
```

A datasource that exists but belongs to a different tenant is returned as 404 Not Found (indistinguishable from non-existence). This prevents tenant A from discovering that tenant B has a datasource with a particular ID.

#### 8.1.4 Test Response Schema

```python
class TestConnectionResponse(BaseModel):
    success:    bool
    latency_ms: int
    category:   Optional[str] = None  # AUTH_FAILED | HOST_UNREACHABLE | ...
    message:    Optional[str] = None  # Human-readable explanation
```

HTTP status is always 200 — a connection failure is not an HTTP error. The failure is encoded in the response body. This is intentional: the test endpoint always succeeds (it successfully performed the test), but the result of the test may be a failure. Treating connection failure as HTTP 5xx would confuse API clients that treat non-2xx as infrastructure errors.

#### 8.1.5 Non-Destructive Guarantee

All three drivers execute only a minimal read-only query:

- PostgreSQL: `SELECT 1` (via `conn.fetchval`)
- Oracle: `SELECT 1 FROM DUAL` (via cursor)
- MSSQL: `SELECT 1 AS connected` (via cursor)

These queries:

- Return immediately (< 1ms on a healthy connection)
- Require no specific table permissions
- Write no data
- Create no objects
- Are safe to run repeatedly at any frequency

#### 8.1.6 Latency Measurement

```python
start_ms = int(time.time() * 1000)
conn     = await asyncpg.connect(...)
await conn.fetchval("SELECT 1")
latency  = int(time.time() * 1000) - start_ms
```

The latency includes both the connection establishment time and the query execution time. For nearby databases, this is typically 5–50ms. For databases over WAN or with slow auth (e.g., Azure AD token validation), it can be 200–2000ms.

---

### 8.2 Frontend Implementation (US 107150)

#### 8.2.1 Test State in `CredentialModal`

Test state is managed as local component state (not a custom hook, unlike v1):

```typescript
const [testing, setTesting] = useState(false);
const [testResult, setTestResult] = useState<{
  success: boolean;
  latency_ms?: number;
  message?: string;
} | null>(null);
const [saving, setSaving] = useState(false);
const [saveError, setSaveError] = useState<string | null>(null);
const [resolvedCredentials, setResolvedCredentials] = useState<Record<
  string,
  unknown
> | null>(null);
const [resolvedTlsExtra, setResolvedTlsExtra] = useState<
  Record<string, string | null>
>({});
```

#### 8.2.2 Test Result Display

```typescript
{testResult?.success && (
  <div className="pill pill-green" style={{ alignSelf: 'flex-start', padding: '6px 12px' }}>
    <Icon name="check" size={14} />
    Connection successful
    {testResult.latency_ms != null ? ` · ${testResult.latency_ms}ms` : ''}
  </div>
)}

{testResult && !testResult.success && (
  <div className="pill pill-red" style={{ alignSelf: 'flex-start', padding: '6px 12px' }}>
    ⚠ {testResult.message || 'Connection failed'}
  </div>
)}
```

The test result displays as a pill badge (green for success, red for failure). Success shows latency in milliseconds. Failure shows the backend's human-readable error message (e.g., "Authentication failed. Check your username, password, or token.").

#### 8.2.3 Save Button Gating

```typescript
<button
  className="btn btn-primary"
  onClick={handleSave}
  disabled={!testResult?.success || saving}   // ← Gated until test succeeds
>
  <Icon name="check" size={14} />
  {saving ? 'Connecting…' : 'Connect'}
</button>
```

#### 8.2.4 `resetTest` — Re-testing After Field Changes

```typescript
function resetTest() {
  setTestResult(null);
  setSaveError(null);
  setResolvedCredentials(null); // ← Force fresh file uploads if fields changed
  setResolvedTlsExtra({});
}
```

`resetTest()` is called on **every form field change**. This means if a user tests successfully then changes the password, the save button is disabled until they test again with the new password. This prevents saving a configuration that was tested with different values.

#### 8.2.5 Re-Test from List (TableBrowserView)

```typescript
async function handleRetest() {
  setRetesting(true);
  try {
    await triggerRetest(source.id); // POST /{id}/test
    onRetested({
      // Optimistic update
      ...source,
      last_tested_at: new Date().toISOString(),
      last_test_status: "success",
    });
  } catch {
    onRetested({
      ...source,
      last_tested_at: new Date().toISOString(),
      last_test_status: "failed",
    });
  } finally {
    setRetesting(false);
  }
}
```

The re-test from the browser view calls `POST /{id}/test` (which uses stored credentials). The result is applied optimistically via `onRetested` callback, which calls `mutateDatasources` in the parent to update the SWR cache without a network refetch.

---

## 9. US 107151 — Permission-Scoped Object Browser

### User Story

> As a **platform user**, after a successful connection test, I want to see a summary of all database objects (schemas, tables, views) that my authenticated user has permission to access.

### Acceptance Criteria (from specification)

- Object Summary panel displayed after successful test
- Summary grouped by: Schemas, Tables, Views
- Objects fetched using authenticated user's permissions only
- Expandable tree: Schema → Tables/Views → Column count
- Loading state while introspection runs
- Handles 1000+ tables without UI freeze
- Per-engine metadata tables used (ALL_TABLES, information_schema, INFORMATION_SCHEMA)

**New in v2:** The object browser is now a full page view (not a sidebar panel), with paginated table cards, server-side search, re-test, and schema sync.

---

### 9.1 Backend Implementation (US 107151)

The schema inspector was completely rewritten in v2 using the **Strategy Pattern** (Abstract Base Class + concrete driver implementations). This is the most architecturally significant change in the v2 backend.

#### 9.1.1 Strategy Pattern Architecture

```
Abstract Base Class (EngineDriver)
├── inspect()  → dict    (all schemas, tables, views — summary view)
├── browse()   → dict    (one schema, paginated)
└── search()   → dict    (one schema, name search)

Concrete Implementations:
├── PostgresDriver(EngineDriver)  — uses asyncpg async
├── OracleDriver(EngineDriver)    — uses python-oracledb async
└── MSSQLDriver(EngineDriver)     — uses pyodbc sync wrapped in asyncio.to_thread

Factory:
└── _get_driver(config) → EngineDriver   (dispatches by engine string)
```

```python
class EngineDriver(ABC):
    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def inspect(self) -> dict:
        """Return all visible schemas with table/view groups."""

    @abstractmethod
    async def browse(self, schema_name: str, offset: int, limit: int) -> dict:
        """Return one page of tables/views for a schema."""

    @abstractmethod
    async def search(self, schema_name: str, search_query: str) -> dict:
        """Return tables/views matching a name search within a schema."""
```

**Why ABC/Strategy over v1's procedural dispatch?**

v1 used `INSPECTORS = {"postgresql": inspect_postgresql, "oracle": inspect_oracle, ...}` with separate top-level functions for inspect, browse, and search. This led to:

- Three separate dispatch tables to maintain
- Connection logic repeated across inspect/browse/search functions
- No natural grouping of per-engine logic

v2's ABC approach:

- Each `EngineDriver` subclass owns its own `_connect()` method and all three operations
- Adding a new engine is adding a new subclass — no dispatch tables to update
- The connection parameters (host, port, auth) are encapsulated in `self.config`
- `ABC` + `@abstractmethod` causes a `TypeError` at startup if a subclass doesn't implement all three operations

#### 9.1.2 Public API Functions

```python
async def discover_schema(config: dict) -> dict:
    driver = _get_driver(config)
    return await asyncio.wait_for(driver.inspect(), timeout=_SCHEMA_TIMEOUT)

async def browse_schema_tables(config, schema_name, offset=0, limit=10) -> dict:
    driver = _get_driver(config)
    return await asyncio.wait_for(
        driver.browse(schema_name, offset, limit),
        timeout=_SCHEMA_TIMEOUT,
    )

async def search_schema_tables(config, schema_name, search_query) -> dict:
    if not search_query or not search_query.strip():
        raise ValueError("search_query must not be empty")
    driver = _get_driver(config)
    return await asyncio.wait_for(
        driver.search(schema_name, search_query.strip()),
        timeout=_SCHEMA_TIMEOUT,
    )

def _get_driver(config: dict) -> EngineDriver:
    engine = config.get("engine", "")
    engine_str = engine.value if hasattr(engine, "value") else str(engine)
    driver_cls = _DRIVERS.get(engine_str)
    if driver_cls is None:
        raise ValueError(f"Schema inspection not supported for engine: '{engine_str}'")
    return driver_cls(config)
```

`_engine_value()` handles both raw strings and `EngineType` enum values — the schema inspector can be called from contexts where the engine is either.

#### 9.1.3 PostgresDriver — All Three Operations

```python
class PostgresDriver(EngineDriver):
    async def _connect(self) -> asyncpg.Connection:
        credentials = self.config["credentials"]
        return await asyncpg.connect(
            host     = self.config["host"],
            port     = int(self.config["port"]),
            database = self.config["database"],
            user     = credentials["username"],
            password = credentials["password"],
            ssl      = _build_ssl_context(self.config.get("tls") or {}),
            timeout  = 10.0,
        )

    async def inspect(self) -> dict:
        conn = await self._connect()
        try:
            rows = await conn.fetch("""
                SELECT
                    t.table_schema,
                    t.table_name,
                    t.table_type,
                    COUNT(c.column_name)::int AS column_count
                FROM information_schema.tables t
                LEFT JOIN information_schema.columns c
                       ON c.table_schema = t.table_schema
                      AND c.table_name   = t.table_name
                WHERE t.table_schema NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                  AND t.table_schema NOT LIKE 'pg_%'
                GROUP BY t.table_schema, t.table_name, t.table_type
                ORDER BY t.table_schema, t.table_name
            """)
            return _build_result(rows, ...)
        finally:
            await _close_async(conn)
```

**Why `information_schema`?** PostgreSQL's `information_schema.tables` automatically filters to only objects the connected user has `SELECT` privilege on. No explicit permission check needed — the database enforces it. This is the key to "permission-scoped" browsing.

**Why filter out `pg_%` schemas?** These are PostgreSQL internal schemas (`pg_catalog`, `pg_toast`, `pg_temp_*`). Users rarely have data in them and they clutter the browser. The filter keeps the view focused on user schemas.

```python
    async def browse(self, schema_name: str, offset: int, limit: int) -> dict:
        conn = await self._connect()
        try:
            # Query 1: Total counts for this schema (for has_more + header display)
            count_row = await conn.fetchrow("""
                SELECT
                    SUM(CASE WHEN table_type = 'BASE TABLE' THEN 1 ELSE 0 END)::int AS total_tables,
                    SUM(CASE WHEN table_type = 'VIEW'       THEN 1 ELSE 0 END)::int AS total_views
                FROM information_schema.tables
                WHERE table_schema = $1
            """, schema_name)

            # Query 2: Paginated objects with row counts from stats
            rows = await conn.fetch("""
                SELECT
                    t.table_name,
                    t.table_type,
                    COUNT(c.column_name)::int AS column_count,
                    COALESCE(s.n_live_tup, 0)::int AS row_count   -- ← From pg_stat_user_tables
                FROM information_schema.tables t
                LEFT JOIN information_schema.columns c ON ...
                LEFT JOIN pg_stat_user_tables s
                       ON s.schemaname = t.table_schema
                      AND s.relname = t.table_name
                WHERE t.table_schema = $1
                GROUP BY t.table_name, t.table_type, s.n_live_tup
                ORDER BY
                    CASE WHEN t.table_type = 'BASE TABLE' THEN 0 ELSE 1 END,  -- Tables before views
                    t.table_name
                LIMIT $2 OFFSET $3
            """, schema_name, limit, offset)

            objects = [_postgres_object(row) for row in rows]
            return _build_browse_result(objects, total_tables, total_views, offset, limit)
        finally:
            await _close_async(conn)
```

`pg_stat_user_tables.n_live_tup` provides an approximate row count from PostgreSQL's autovacuum statistics — much faster than `COUNT(*)` on large tables. "Approximate" is acceptable for the object browser (exact counts are for M3 query results).

```python
    async def search(self, schema_name: str, search_query: str) -> dict:
        conn = await self._connect()
        try:
            rows = await conn.fetch("""
                ...
                WHERE t.table_schema = $1
                  AND LOWER(t.table_name) LIKE LOWER($2)   -- ← Case-insensitive partial match
                ...
            """, schema_name, f"%{search_query}%")

            objects = [_postgres_object(row) for row in rows]
            return {"objects": objects, "total": len(objects)}
        finally:
            await _close_async(conn)
```

Search uses `LOWER(name) LIKE LOWER(query)` for case-insensitive partial matching. The `%` prefix and suffix allow matching at any position in the name. No pagination on search results — the assumption is that search returns a small, focused set.

#### 9.1.4 OracleDriver — All Three Operations

```python
class OracleDriver(EngineDriver):
    async def _connect(self):
        import oracledb
        auth_method = self.config["auth_method"]
        credentials = self.config["credentials"]
        dsn = _build_connect_string(self.config)

        if auth_method == "password":
            return await oracledb.connect_async(user=..., password=..., dsn=dsn, ...)
        elif auth_method == "wallet":
            return await oracledb.connect_async(dsn=dsn, wallet_location=..., ...)
        elif auth_method == "kerberos":
            return await oracledb.connect_async(user=f"/{credentials['principal']}",
                                                 externalauth=True, ...)

    async def inspect(self) -> dict:
        conn = await self._connect()
        cursor = conn.cursor()
        try:
            await cursor.execute("""
                SELECT
                    ao.owner AS schema_name,
                    ao.object_name,
                    ao.object_type,
                    NVL(
                        (SELECT COUNT(*) FROM all_tab_columns atc
                         WHERE atc.owner = ao.owner AND atc.table_name = ao.object_name),
                        0
                    ) AS column_count
                FROM all_objects ao
                WHERE ao.object_type IN ('TABLE', 'VIEW')
                ORDER BY ao.owner, ao.object_name
            """)
```

**Why `ALL_OBJECTS`?** Oracle's `ALL_OBJECTS` shows objects the authenticated user has `SELECT` or any other privilege on. This is Oracle's equivalent of PostgreSQL's `information_schema` permission scoping. Comparing to `DBA_OBJECTS` (which shows everything regardless of permissions) would violate the "permission-scoped" requirement.

```python
    async def browse(self, schema_name: str, offset: int, limit: int) -> dict:
        conn = await self._connect()
        cursor = conn.cursor()
        try:
            # Count query
            await cursor.execute("""
                SELECT
                    SUM(CASE WHEN object_type = 'TABLE' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN object_type = 'VIEW'  THEN 1 ELSE 0 END)
                FROM all_objects
                WHERE owner = UPPER(:schema_name)  -- Oracle is case-sensitive: needs UPPER()
                  AND object_type IN ('TABLE', 'VIEW')
            """, schema_name=schema_name)

            # Paginated objects (Oracle's OFFSET...FETCH syntax, 12c+)
            await cursor.execute("""
                SELECT
                    ao.object_name,
                    ao.object_type,
                    NVL(...column_count...) AS column_count,
                    NVL(
                        (SELECT num_rows FROM all_tables at
                         WHERE at.owner = ao.owner AND at.table_name = ao.object_name),
                        0
                    ) AS row_count      -- ← From ALL_TABLES.NUM_ROWS (stats from DBMS_STATS)
                FROM all_objects ao
                WHERE ao.owner = UPPER(:schema_name)
                  AND ao.object_type IN ('TABLE', 'VIEW')
                ORDER BY ...
                OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
            """, ...)
```

Oracle's `ALL_TABLES.NUM_ROWS` is populated by `DBMS_STATS.GATHER_TABLE_STATS` (analogous to PostgreSQL's autovacuum). It may be NULL if statistics were never gathered, hence the `NVL(..., 0)` default.

`UPPER(:schema_name)` is necessary because Oracle stores object names in UPPERCASE by default. Passing a lowercase schema name would return zero results without this conversion.

#### 9.1.5 MSSQLDriver — Synchronous + Thread Pool

```python
class MSSQLDriver(EngineDriver):
    async def inspect(self) -> dict:
        return await asyncio.to_thread(self._sync_inspect)

    async def browse(self, schema_name: str, offset: int, limit: int) -> dict:
        return await asyncio.to_thread(self._sync_browse, schema_name, offset, limit)

    async def search(self, schema_name: str, search_query: str) -> dict:
        return await asyncio.to_thread(self._sync_search, schema_name, search_query)
```

All three MSSQL operations are implemented as synchronous methods and run in `asyncio.to_thread()`. This is necessary because `pyodbc` is synchronous (no async API). Running `pyodbc.connect()` directly in an async function would block the FastAPI event loop for the duration of the connection.

```python
    def _sync_browse(self, schema_name: str, offset: int, limit: int) -> dict:
        conn = self._connect()
        cursor = conn.cursor()
        try:
            # Row count via sys.partitions (faster than COUNT(*) for large tables)
            cursor.execute("""
                WITH row_counts AS (
                    SELECT object_id, SUM(rows) AS row_count
                    FROM sys.partitions
                    WHERE index_id IN (0, 1)   -- 0=heap, 1=clustered index
                    GROUP BY object_id
                )
                SELECT
                    t.TABLE_NAME,
                    t.TABLE_TYPE,
                    COUNT(c.COLUMN_NAME) AS column_count,
                    ISNULL(rc.row_count, 0) AS row_count
                FROM INFORMATION_SCHEMA.TABLES t
                LEFT JOIN INFORMATION_SCHEMA.COLUMNS c ON ...
                LEFT JOIN row_counts rc
                       ON rc.object_id = OBJECT_ID(
                           QUOTENAME(t.TABLE_SCHEMA) + '.' + QUOTENAME(t.TABLE_NAME)
                       )
                WHERE t.TABLE_SCHEMA = ?
                GROUP BY t.TABLE_NAME, t.TABLE_TYPE, rc.row_count
                ORDER BY
                    CASE WHEN t.TABLE_TYPE = 'BASE TABLE' THEN 0 ELSE 1 END,
                    t.TABLE_NAME
                OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
            """, schema_name, offset, limit)
```

`sys.partitions` provides approximate row counts without a full table scan, similar to `pg_stat_user_tables` in PostgreSQL and `ALL_TABLES.NUM_ROWS` in Oracle. `QUOTENAME()` safely escapes schema and table names to prevent SQL injection in the `OBJECT_ID()` call.

#### 9.1.6 Result Builder Helpers

```python
def _build_result(rows, *, schema_col, name_col, type_col, count_col) -> dict:
    """Converts flat DB result rows into nested {namespaces: [...]} structure."""
    namespaces: dict[str, dict] = {}
    for row in rows:
        schema    = str(_row_get(row, schema_col))
        name      = str(_row_get(row, name_col))
        obj_type  = str(_row_get(row, type_col)).upper()
        col_count = int(_row_get(row, count_col, 0) or 0)

        if schema not in namespaces:
            namespaces[schema] = {"name": schema, "tables": [], "views": []}

        obj = {"name": name, "type": obj_type, "column_count": col_count}
        if "VIEW" in obj_type:
            namespaces[schema]["views"].append(obj)
        else:
            namespaces[schema]["tables"].append(obj)

    ns_list = list(namespaces.values())
    return {
        "namespaces": ns_list,
        "summary": {
            "total_schemas": len(ns_list),
            "total_tables":  sum(len(ns["tables"]) for ns in ns_list),
            "total_views":   sum(len(ns["views"]) for ns in ns_list),
        },
    }

def _build_browse_result(objects, total_tables, total_views, offset, limit) -> dict:
    return {
        "objects":      objects,
        "total_tables": total_tables,
        "total_views":  total_views,
        "offset":       offset,
        "limit":        limit,
        "has_more":     (offset + len(objects)) < (total_tables + total_views),
    }
```

These helpers abstract over the different row formats (asyncpg `Record` objects, Oracle tuple lists, pyodbc `Row` objects) via `_row_get`, which handles both dict-like and sequence-like row objects.

Per-engine normalisation functions map raw driver results to the common shape:

```python
def _postgres_object(row) -> dict:
    return {
        "name":         row["table_name"],
        "type":         "TABLE" if row["table_type"] == "BASE TABLE" else "VIEW",
        "column_count": int(row["column_count"] or 0),
        "row_count":    int(row["row_count"] or 0),
    }

def _oracle_object(row) -> dict:
    return {
        "name":         row[0],
        "type":         row[1],          # Already "TABLE" or "VIEW" from Oracle
        "column_count": int(row[2] or 0),
        "row_count":    int(row[3] or 0),
    }

def _mssql_object(row) -> dict:
    return {
        "name":         row[0],
        "type":         "TABLE" if row[1] == "BASE TABLE" else "VIEW",  # Normalise MSSQL label
        "column_count": int(row[2] or 0),
        "row_count":    int(row[3] or 0),
    }
```

#### 9.1.7 Three New API Endpoints

**`GET /{datasource_id}/schema`** — Full schema discovery (all namespaces):

```python
@router.get("/{datasource_id}/schema", response_model=SchemaDiscoveryResponse)
async def get_datasource_schema(datasource_id, current_user, db):
    result = await service.get_datasource_schema(
        datasource_id = datasource_id,
        tenant_id     = current_user["tenant_id"],
        db            = db,
    )
    return SchemaDiscoveryResponse(**result)
```

**`GET /{datasource_id}/tables`** — Paginated table browse for one schema:

```python
@router.get("/{datasource_id}/tables", response_model=TableBrowseResponse)
async def browse_schema_tables(
    datasource_id: str,
    schema_name:   str           = Query(..., min_length=1),
    offset:        int           = Query(0,   ge=0),
    limit:         int           = Query(10,  ge=1, le=200),   # ← 200 max per page
    ...
):
    result = await service.browse_datasource_tables(
        datasource_id, schema_name, offset, limit, tenant_id, db
    )
    return TableBrowseResponse(**result)
```

**`GET /{datasource_id}/search`** — Name-based search within a schema:

```python
@router.get("/{datasource_id}/search", response_model=SearchTableResponse)
async def search_datasource_tables(
    datasource_id: str,
    schema_name:   str = Query(..., min_length=1),
    query:         str = Query(..., min_length=1),
    ...
):
    result = await service.search_datasource_tables(
        datasource_id, schema_name, query, tenant_id, db
    )
    return SearchTableResponse(**result)
```

---

### 9.2 Frontend Implementation (US 107151)

The object browser is implemented as `TableBrowserView`, a sub-component of `datasource/page.tsx`. It is rendered when `selectedSource !== null` (user clicked a datasource row in the list).

#### 9.2.1 Data Loading Strategy

The browser fetches ALL tables for a schema upfront using the `browse` endpoint with a large page size (100), looping until `has_more === false`:

```typescript
async function loadAllTables() {
  setLoading(true);
  setError(null);
  setSearchResults(null);
  setCurrentPage(1);

  try {
    let allData: SchemaObject[] = [];
    let offset = 0;
    let hasMore = true;

    while (hasMore) {
      const data = await triggerBrowse({ offset });
      if (!data) break;
      allData = allData.concat(data.objects);
      hasMore = data.has_more;
      offset += 100;
    }

    setAllObjects(allData);
  } catch (err) {
    setError(err instanceof Error ? err.message : "Failed to load tables");
  } finally {
    setLoading(false);
  }
}
```

**Tradeoff:** Fetching all tables upfront allows client-side pagination (no server round-trip per page) and makes client-side search instant for large schemas. The downside is that very large schemas (10,000+ tables) would be slow to load. For most enterprise OLAP databases (typically 50–500 tables per schema), this is acceptable.

#### 9.2.2 Client-Side Pagination

```typescript
const PAGE_SIZE = 10;
const displayObjects = searchResults ?? allObjects; // Search results or full list
const totalPages = Math.ceil(displayObjects.length / PAGE_SIZE);
const startIdx = (currentPage - 1) * PAGE_SIZE;
const endIdx = startIdx + PAGE_SIZE;
const pageObjects = displayObjects.slice(startIdx, endIdx);
```

Pagination is purely client-side using array slicing. The `displayObjects` source switches between `allObjects` (full list) and `searchResults` (filtered subset) based on whether a search query is active.

#### 9.2.3 Server-Side Search

```typescript
async function handleSearch(query: string) {
  setSearchQuery(query);
  if (!query.trim()) {
    setSearchResults(null); // ← Clear search, show all tables
    setCurrentPage(1);
    return;
  }

  setIsSearching(true);
  try {
    const result = await triggerSearch(query.trim());
    setSearchResults(result?.objects ?? []);
    setCurrentPage(1); // ← Reset to first page on each search
  } catch (err) {
    setError("Search failed");
  } finally {
    setIsSearching(false);
  }
}
```

The search is triggered on every keystroke (no debounce currently — the `onChange` directly calls `handleSearch`). A debounce of 300–500ms would be appropriate for production to avoid excessive server calls. Search results replace the locally-fetched `allObjects` as the display source.

#### 9.2.4 `TableCard` Component

```typescript
function TableCard({ obj }: { obj: SchemaObject }) {
  const isView    = obj.type === 'VIEW';
  const rowsLabel = obj.row_count > 0 ? obj.row_count.toLocaleString() : '—';

  return (
    <div style={{ padding: '16px', borderRadius: '8px', border: '1px solid var(--border-soft)', ... }}>
      {/* Header: icon + name + type badge */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
        <span style={{ fontSize: 18 }}>{isView ? '👁' : '📋'}</span>
        <div>
          <div style={{ fontFamily: 'var(--mono)', fontWeight: 700 }}>{obj.name}</div>
          <span className="pill" style={{
            backgroundColor: isView ? 'oklch(0.4 0.15 280)' : 'var(--accent)',
          }}>
            {obj.type}
          </span>
        </div>
      </div>

      {/* Stats: column count + row count */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', ... }}>
        <div><small>Columns</small><strong>{obj.column_count}</strong></div>
        <div><small>Rows</small><strong>{rowsLabel}</strong></div>
      </div>
    </div>
  );
}
```

The card uses `var(--mono)` (IBM Plex Mono) for the table name to visually distinguish it as a technical identifier. Views get a purple (`oklch(0.4 0.15 280)`) badge, tables get the accent blue. Row count shows `—` when zero (which happens when database statistics haven't been gathered or for views).

#### 9.2.5 Pagination Controls

```typescript
{!searchResults && totalPages > 1 && (
  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', ... }}>
    {/* First / Prev buttons */}
    <button onClick={() => setCurrentPage(1)} disabled={currentPage === 1}>⟨⟨</button>
    <button onClick={() => setCurrentPage(p => Math.max(1, p - 1))} disabled={currentPage === 1}>⟨</button>

    {/* Page numbers: show up to 5, centered around current page */}
    {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
      const startPage = Math.max(1, currentPage - Math.floor(5 / 2));
      const page      = startPage + i;
      if (page > totalPages) return null;
      return (
        <button
          className={`btn btn-sm ${currentPage === page ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => setCurrentPage(page)}
        >
          {page}
        </button>
      );
    })}

    {/* Next / Last buttons */}
    <button onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))} disabled={currentPage === totalPages}>⟩</button>
    <button onClick={() => setCurrentPage(totalPages)} disabled={currentPage === totalPages}>⟩⟩</button>
  </div>
)}
```

Pagination is hidden when viewing search results (search returns all matches, no pagination) and when there's only one page.

#### 9.2.6 Summary Header in Browser View

```typescript
<div className="between" style={{ margin: '6px 0 22px' }}>
  <div className="row">
    <DBLogo slug={meta.slug} size={44} radius={12} letter={meta.letter} color={meta.color} />
    <div>
      <h1 className="section-title">{source.name}</h1>
      <div className="faint mono">
        {source.host}:{source.port}/{source.database_name}
      </div>
    </div>
  </div>

  <div className="row">
    <span className={`pill ${statusClass}`}>{statusLabel}</span>   {/* Connected / Failed / Not tested */}
    <button onClick={handleRetest} disabled={retesting}>
      <Icon name="refresh" /> {retesting ? 'Testing…' : 'Re-test'}
    </button>
    <button onClick={handleSync} disabled={syncing || loading}>
      <Icon name="refresh" /> {syncing ? 'Syncing…' : 'Sync now'}
    </button>
  </div>
</div>
```

"Sync now" triggers `loadAllTables()`, re-fetching all tables from the server. This handles schema changes (new tables, dropped tables) without navigating away.

#### 9.2.7 TypeScript Interfaces for M1

**File:** `lib/types/interface/features/datasource.interface.ts`

```typescript
export interface SchemaObject {
  name: string;
  type: "TABLE" | "VIEW";
  column_count: number;
  row_count: number;
}

export interface TableBrowseResult {
  datasource_id: string;
  datasource_name: string;
  engine: EngineType;
  schema_name: string;
  objects: SchemaObject[];
  total_tables: number; // ← Total across ALL pages (not just this page)
  total_views: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

export interface SearchTableResult {
  datasource_id: string;
  datasource_name: string;
  engine: EngineType;
  schema_name: string;
  objects: SchemaObject[];
  total: number; // ← All matches (no pagination on search)
}
```

---

## 10. Cross-Cutting Concerns

### 10.1 Tenant Isolation

Every database query that reads or writes datasource records includes a `tenant_id` filter:

```python
# List: always filters by tenant
select(Datasource).where(Datasource.tenant_id == tenant_id)

# Get by ID: ALSO filters by tenant (not just ID)
select(Datasource).where(
    Datasource.id        == uuid.UUID(datasource_id),
    Datasource.tenant_id == tenant_id,
)
```

This means a tenant can never access another tenant's datasources, even by guessing or brute-forcing UUIDs. A request for a valid UUID that belongs to the wrong tenant returns the same 404 response as a completely nonexistent UUID.

**Current state:** `tenant_id` is hardcoded to `"dev-tenant-001"` via `get_current_user()`. In M10, this will come from the Keycloak token's `tenant` claim.

### 10.2 Security Posture Summary

| Concern                               | Mechanism                                                                   |
| ------------------------------------- | --------------------------------------------------------------------------- |
| Credential confidentiality at rest    | AES-256-GCM encryption with random IV per encryption                        |
| Credential integrity at rest          | GCM auth tag — tampered ciphertext fails with `InvalidTag`                  |
| Credential confidentiality in transit | API always returns `has_credentials: bool`, never credential values         |
| File upload safety                    | Extension whitelist + type whitelist + size limit + non-guessable filenames |
| Multi-tenant isolation                | `tenant_id` filter on every DB query                                        |
| Authentication                        | Keycloak PKCE S256 (scaffolded, full enforcement in M10)                    |
| SQL injection                         | Parameterised queries throughout (`$1`, `:param`, `?`)                      |
| Path traversal                        | No user-controlled path components in file storage                          |

### 10.3 `Icon` Component

The `Icon` component provides a complete SVG icon set using inline path definitions:

```typescript
const PATHS: Record<string, string> = {
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/>...',
  search:   '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  // ... 40+ icons
};

export default function Icon({ name, size = 20, stroke = 1.7, className, style }) {
  const path = PATHS[name] ?? '';
  return (
    <svg
      width={size} height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      dangerouslySetInnerHTML={{ __html: path }}
    />
  );
}
```

All icons use `stroke="currentColor"` — they inherit the text colour from their parent, adapting automatically to light/dark modes and hover states without any prop configuration.

`dangerouslySetInnerHTML` is safe here because `PATHS` is a static constant defined at compile time, not user input. The `name` prop from outside only selects from this known-safe dict; if the name isn't found, `path = ''` produces an empty SVG.

### 10.4 SWR Data Fetching Pattern

The datasource page uses SWR consistently for all data operations:

**Declarative reads (`useSWR`):**

```typescript
// Key = URL; fetcher = get<T> from fetch.utils.ts
const { data, isLoading, error, mutate } = useSWR(getDatasources, (url) =>
  get<DatasourceListResponse>(url).then((r) => r.data),
);
```

**Imperative mutations (`useSWRMutation`):**

```typescript
const { trigger: triggerCreate } = useSWRMutation(
  postDatasource,
  (url, { arg }: { arg: DatasourcePayload }) =>
    post<DatasourceRecord, DatasourcePayload>(url, arg),
);
// Usage: await triggerCreate(payload)
```

This pattern separates:

- **State management:** SWR handles loading, error, caching, revalidation
- **Business logic:** Component handles when to trigger mutations
- **Optimistic updates:** `mutate()` updates cache immediately; server re-validates in background

### 10.5 CSS Design System Conventions

The `design.css` file establishes conventions all M1 (and future) components follow:

**Spacing:** Uses `calc(Npx * var(--pad))` where `--pad: 1`. This means any spacing can be scaled globally by changing `--pad` (useful for compact/comfortable modes in M10 user preferences).

**Transitions:** All interactive elements use `transition: X .15s var(--ease)` where `--ease: cubic-bezier(0.22, 0.61, 0.36, 1)` (a slightly bouncy ease-out). This creates a unified "snap" feel across all interactions.

**Typography:** `Plus Jakarta Sans` (Latin-script, rounded, approachable) for UI text; `IBM Plex Mono` (technical precision) for code, paths, and database identifiers.

**Colour conventions:**

- `var(--text)` for primary content
- `var(--text-muted)` for secondary/labels
- `var(--text-faint)` for metadata/timestamps
- `var(--accent)` for interactive elements
- `var(--danger)` for destructive actions (delete, error states)

---

## 11. API Reference

### 11.1 Endpoints Summary

Base path: `/api/v1/datasources`

| Method   | Path           | Purpose                         | Body                      | Response                   |
| -------- | -------------- | ------------------------------- | ------------------------- | -------------------------- |
| `POST`   | `/test`        | Pre-save connection test        | `DatasourcePayload`       | `TestConnectionResponse`   |
| `POST`   | `/upload`      | Upload TLS cert/wallet/keytab   | `multipart/form-data`     | `FileUploadResponse`       |
| `POST`   | `/`            | Create and save datasource      | `DatasourcePayload`       | `DatasourceResponse` (201) |
| `GET`    | `/`            | List all datasources for tenant | —                         | `DatasourceListResponse`   |
| `POST`   | `/{id}/test`   | Re-test saved datasource        | —                         | `TestConnectionResponse`   |
| `GET`    | `/{id}/schema` | Full schema discovery           | —                         | `SchemaDiscoveryResponse`  |
| `GET`    | `/{id}/tables` | Paginated table browse          | ?schema_name&offset&limit | `TableBrowseResponse`      |
| `GET`    | `/{id}/search` | Table name search               | ?schema_name&query        | `SearchTableResponse`      |
| `DELETE` | `/{id}`        | Delete datasource               | —                         | 204 No Content             |

### 11.2 Error Codes

| HTTP | When                                                                           |
| ---- | ------------------------------------------------------------------------------ |
| 200  | All GET and re-test/schema endpoints                                           |
| 201  | `POST /` — datasource created                                                  |
| 204  | `DELETE /{id}` — deleted                                                       |
| 400  | Invalid upload type or file extension                                          |
| 404  | Datasource not found (or belongs to different tenant)                          |
| 409  | `POST /` — duplicate datasource name within tenant                             |
| 413  | Upload file exceeds `MAX_UPLOAD_SIZE_MB`                                       |
| 422  | Pydantic validation failure (invalid payload, missing fields, rule violations) |
| 500  | Decryption failure (wrong key, tampered data)                                  |
| 502  | Schema discovery/browse/search failure (connection to target DB failed)        |

### 11.3 Test Result Categories

| Category               | Meaning                                 | Typical cause                                  |
| ---------------------- | --------------------------------------- | ---------------------------------------------- |
| ✅ (success)           | Connection and SELECT 1 succeeded       | —                                              |
| `AUTH_FAILED`          | Authentication rejected                 | Wrong password, expired token, wrong wallet    |
| `HOST_UNREACHABLE`     | TCP connection failed                   | Wrong host, firewall, no listener, DNS failure |
| `TLS_HANDSHAKE_FAILED` | TLS negotiation failed                  | Cert mismatch, wrong mode, expired cert        |
| `TIMEOUT`              | No response within 10 seconds           | Slow network, VPN, overloaded DB               |
| `UNSUPPORTED_CONFIG`   | Config requires unavailable server deps | Kerberos without Thick Mode, no ODBC driver    |
| `UNSUPPORTED_ENGINE`   | Unknown engine string in config         | Programming error                              |
| `UNKNOWN`              | Uncategorised error                     | Unusual errors; check `message` field          |

---

## 12. Data Flow Diagrams

### 12.1 Register New Datasource (Happy Path)

```
User fills CredentialModal (name, host, port, db, schema, auth, TLS)
    │
    ▼ (click "Test connection")
1. resolveUploads()
   ├── If wallet/keytab: POST /upload → FileUploadResponse (server path)
   ├── If CA cert: POST /upload → FileUploadResponse
   └── Returns: { credentials: {with paths}, tlsExtra: {tls paths} }
    │
    ▼
2. buildPayload(resolvedCredentials, resolvedTlsExtra)
    │
    ▼
3. POST /api/v1/datasources/test
   Backend:
   ├── Pydantic validation (incl. engine+auth cross-field check)
   ├── service.test_datasource_connection(payload)
   │   └── test_connection(config)
   │       ├── asyncio.wait_for(driver_fn(config), timeout=10)
   │       ├── driver: connect → SELECT 1 → measure latency
   │       └── Return { success, latency_ms }
   └── TestConnectionResponse { success: true, latency_ms: 145 }
    │
    ▼ testResult.success === true
4. resolvedCredentials + resolvedTlsExtra stored in state
   "Connect" button enabled
    │
    ▼ (click "Connect")
5. POST /api/v1/datasources/ (same payload, stored resolved state)
   Backend:
   ├── Pydantic validation
   ├── service.create_datasource(payload, tenant_id, user_id)
   │   ├── SELECT WHERE name + tenant_id (conflict check)
   │   ├── encrypt(credentials.model_dump()) → "iv:tag:ciphertext"
   │   ├── Datasource(...) + db.add() + db.flush()
   │   └── _mask_sensitive_fields(datasource)
   └── DatasourceResponse (201 Created, no credentials in response)
    │
    ▼
6. onCreated() → mutateDatasources() → SWR re-fetches list
   onClose() → modal closes
   Datasource appears in "Connected sources" list
```

### 12.2 Schema Browse (Happy Path)

```
User clicks a datasource row in the list
    │
    ▼ selectedSource = source
TableBrowserView mounted
    │
    ▼ useEffect → loadAllTables()
Loop:
1. GET /api/v1/datasources/{id}/tables?schema_name={default_schema}&offset=0&limit=100
   Backend:
   ├── _get_datasource(id, tenant_id)
   ├── _datasource_runtime_config(ds) → decrypt credentials
   ├── browse_schema_tables(config, schema_name, 0, 100)
   │   └── MSSQLDriver/PostgresDriver/OracleDriver.browse(schema, 0, 100)
   │       ├── Execute paginated SQL with column + row counts
   │       └── Return {objects: [...100 items], has_more: true}
   └── TableBrowseResponse

2. If has_more: GET ...&offset=100&limit=100
3. Repeat until has_more === false
    │
    ▼ allObjects filled
4. Display in card grid (10 per page, client-side)
    │
    ▼ User types in search box
5. GET /api/v1/datasources/{id}/search?schema_name={schema}&query={term}
   Backend:
   ├── schema_inspector.search_schema_tables(config, schema, term)
   │   └── Driver.search(schema, term)
   │       └── LIKE query: WHERE LOWER(name) LIKE LOWER('%term%')
   └── SearchTableResponse { objects: [...matches], total: N }
    │
    ▼ searchResults = result.objects
6. Display search results (replacing allObjects as display source)
   Clear search → back to full allObjects list
```

### 12.3 Re-Test Saved Datasource

```
User clicks "Re-test" button in TableBrowserView or list row
    │
    ▼
POST /api/v1/datasources/{id}/test
    │
    ├── _get_datasource(id, tenant_id) → verify ownership
    ├── _datasource_runtime_config(ds)
    │   └── decrypt(encrypted_credentials) → plaintext creds
    ├── test_connection(config)
    │   └── [same as pre-save test, using decrypted credentials]
    ├── ds.last_tested_at = now()
    ├── ds.last_test_status = "success" | "failed"
    └── db.flush()
    │
    ▼ TestConnectionResponse
    │
Frontend:
└── onRetested({ ...source, last_test_status: "success", last_tested_at: now })
    └── mutateDatasources(optimistic update — no refetch)
        └── Status pill updates immediately
```

---

_End of M1 Technical Implementation Documentation v2_
