# InsightX

> **Agentic Reporting Platform** — connect data sources, query them in natural language, and turn results into shareable insights.

---

## Overview

InsightX is a layered application repository structured for frontend-first delivery. The platform allows users to:

- Connect enterprise databases (PostgreSQL, Oracle, MSSQL) with multiple authentication methods
- Query connected data using natural language (NL → SQL)
- Build, save, and share data visualisations (insights)

The repository is organised into five top-level directories. Only `web/` and `api/` are currently active — the others are scaffolded for later phases.

| Directory | Role                                 | Status                  |
| --------- | ------------------------------------ | ----------------------- |
| `web/`    | Next.js frontend                     | ✅ Active (M1 complete) |
| `api/`    | FastAPI backend                      | ✅ Active (M1 complete) |
| `infra/`  | NGINX, IaC, cloud manifests          | 🔲 Placeholder          |
| `job/`    | Background jobs, cron, batch workers | 🔲 Placeholder          |
| `mcp/`    | Model control plane / orchestration  | 🔲 Placeholder          |

---

## Implementation Status

| Module | Feature                                      | Status      |
| ------ | -------------------------------------------- | ----------- |
| M1     | Data Source Onboarding                       | ✅ Complete |
| M2     | Data Annotation (Dictionary Generation)      | ✅ Complete |
| M3     | NL to SQL Generation                         | 🔲 Upcoming |
| M4     | Create Insight                               | 🔲 Upcoming |
| M5     | Insight History & Versioning                 | 🔲 Upcoming |
| M6     | Export Insight                               | 🔲 Upcoming |
| M7     | Notifications & Alerts                       | 🔲 Upcoming |
| M8     | Tools Glossary                               | 🔲 Upcoming |
| M9     | Model Configuration (Cloud & On-Prem)        | 🔲 Upcoming |
| M10    | Authentication & Authorization (RBAC / ABAC) | 🔲 Upcoming |
| M11    | API Specifications, SDK & Widgets            | 🔲 Upcoming |

---

## Directory Structure

```
InsightX/
├── api/                            ← FastAPI backend (Python 3.11+)
│   ├── .env.example                ← Template for required env vars
│   ├── requirements.txt            ← Python dependencies
│   ├── app/
│   │   ├── main.py                 ← App factory: FastAPI, CORS, routers, migration shim
│   │   ├── core/
│   │   │   ├── config.py           ← Pydantic Settings: reads .env file
│   │   │   └── engines_config.py   ← Per-engine auth capability config
│   │   ├── db/
│   │   │   ├── base.py             ← SQLAlchemy DeclarativeBase
│   │   │   ├── session.py          ← Async engine + session factory
│   │   │   └── models/
│   │   │       ├── datasource.py   ← Datasource ORM model
│   │   │       └── annotation.py   ← TableAnnotation, ColumnAnnotation, TableRelationship ORM models
│   │   └── modules/
│   │       ├── datasources/
│   │       │   ├── router.py       ← FastAPI router: 11 endpoints
│   │           ├── schemas.py      ← Pydantic request/response schemas
│   │           ├── service.py      ← Business logic (no HTTP concerns)
│   │           ├── connection_tester.py  ← Dispatch to per-engine driver
│   │           ├── credential_encryptor.py  ← AES-256-GCM encrypt/decrypt
│   │           ├── schema_inspector.py  ← Schema discovery, table browse, search, column meta,etc
│   │           └── drivers/
│   │               ├── postgres_driver.py
│   │               ├── oracle_driver.py
│   │               └── mssql_driver.py
│   │       └── annotations/
│   │           ├── router.py       ← Annotation CRUD + relationship endpoints
│   │           ├── schemas.py      ← Pydantic request/response models for M2
│   │           └── service.py      ← Annotation business logic (upsert, relationships, cascade delete)
│   └── database/
│       └── migrations/
│           ├── 001_create_datasources.sql
│           └── 002_create_annotations.sql  ← table_annotations, column_annotations, table_relationships DDL
│
├── web/                            ← Next.js 16 frontend (TypeScript)
│   ├── next.config.ts              ← API proxy rewrites + allowedDevOrigins
│   ├── tsconfig.json               ← "@/*" alias → web/ root
│   ├── app/
│   │   ├── layout.tsx              ← Root layout
│   │   ├── page.tsx                ← Home route
|   |   ├── providers.tsx
|   |   ├── globals.css
|   |   ├── design.css
│   │   ├── component/
│   │   │   ├── AppShell.tsx        ← Main chrome wrapper
│   │   │   ├── Sidebar.tsx         ← Navigation sidebar
│   │   │   ├── Icon.tsx            ← Icon component
│   │   │   ├── DBLogo.tsx          ← Database engine logo badge
│   │   │   └── ...                 ← Other shared components
│   │   ├── datasource/             ← /datasource route (active data source UI)
│   │   │   └── page.tsx
│   │   ├── dashboard/page.tsx
│   │   ├── insight/page.tsx
│   │   ├── users/page.tsx
│   │   ├── glossary/page.tsx
│   │   └── developers/page.tsx
│   ├── config/
│   │   └── engines.ts              ← Engine metadata: ports, auth methods, TLS modes
│   │   └── url.config.ts
│   ├── hooks/                      ← Shared React hooks
│   └── lib/
│       ├── types/interface/features
│       │   └── auth.interface.ts
│       │   └── datasource.interface.ts
│       │   └── annotation.interface.ts  ← M2 TypeScript interfaces (ColumnMeta, Relationship, etc.)
│       ├── utils/
│       │   └── auth-fetch.utils.ts
│       │   └── fetch.utils.ts
│       └── redux/
│       │   ├── store.ts
│       │   ├── hooks.ts
│       │   └── features/counter/counterSlice.ts
│       └── keycloak.ts
│       └── types.ts
│       └── webcrpyto-polyfill.ts
│
├── infra/                          ← Placeholder
├── job/                            ← Placeholder
├── mcp/                            ← Placeholder
└── README.md
└── CLAUDE.md
```

---

## Prerequisites

### Frontend

| Requirement | Version    |
| ----------- | ---------- |
| Node.js     | 18+        |
| npm         | Any recent |

### Backend

| Requirement                | Version        | Notes                                     |
| -------------------------- | -------------- | ----------------------------------------- |
| Python                     | 3.11+          |                                           |
| PostgreSQL                 | 14+            | Required metadata database                |
| Oracle Instant Client      | 19+ (optional) | Required for Oracle Kerberos (Thick Mode) |
| ODBC Driver for SQL Server | 17 or 18       | Required for MSSQL connections            |

---

## Environment Variables

### Backend (`api/.env`)

```bash
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/insightx

CREDENTIAL_ENCRYPTION_KEY=<64 hex chars>
# Generate: python -c "import secrets; print(secrets.token_hex(32))"

SECURE_FILES_DIR=./secure-uploads
MAX_UPLOAD_SIZE_MB=5
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=20

# Leave empty for dev mode (no Keycloak required)
KEYCLOAK_URL=
KEYCLOAK_REALM=insightx
KEYCLOAK_CLIENT_ID=insightx-backend
KEYCLOAK_CLIENT_SECRET=
INTROSPECT_CACHE_TTL_SECONDS=30
```

---

## Setup & Running

### Backend

```bash
cd api
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Linux/macOS
pip install -r requirements.txt
cp .env.example .env            # fill in values
uvicorn app.main:app --reload --port 8000
```

Interactive API docs: **http://localhost:8000/docs**

### Frontend

```bash
cd web
npm install
npm run dev                     # :3000, hot reload
npm run build                   # type-check + compile
```

---

## Frontend (`web/`)

### Frontend Tech Stack

| Technology    | Version | Role                    |
| ------------- | ------- | ----------------------- |
| Next.js       | 16      | Framework (App Router)  |
| React         | 19      | UI                      |
| TypeScript    | ^5      | Type safety             |
| Tailwind CSS  | ^4      | Utility-first styling   |
| Redux Toolkit | ^2      | Global state management |
| React Redux   | ^9      | React bindings          |

## Backend (`api/`)

### Backend Tech Stack

| Technology      | Version | Role                              |
| --------------- | ------- | --------------------------------- |
| Python          | 3.11+   | Runtime                           |
| FastAPI         | 0.136+  | Web framework                     |
| SQLAlchemy      | 2.x     | ORM (async)                       |
| Pydantic        | v2      | Validation and settings           |
| asyncpg         | latest  | PostgreSQL async driver           |
| python-oracledb | latest  | Oracle driver (Thin + Thick mode) |
| pyodbc          | latest  | MSSQL driver                      |
| cryptography    | latest  | AES-256-GCM credential encryption |

### API Endpoints

Base path: `/api/v1/datasources`

**Datasources** (`/api/v1/datasources`):

| Method   | Path                       | Description                                                             |
| -------- | -------------------------- | ----------------------------------------------------------------------- |
| `POST`   | `/test`                    | Pre-save connection test (plaintext creds, not persisted)               |
| `POST`   | `/upload`                  | Upload a secure file (TLS cert, wallet, keytab); returns server path    |
| `POST`   | `/`                        | Create and save a datasource with AES-256-GCM encrypted credentials     |
| `GET`    | `/`                        | List all datasources for the tenant (credentials stripped)              |
| `POST`   | `/{id}/test`               | Re-test a saved datasource using stored encrypted credentials           |
| `PATCH`  | `/{id}/deactivate`         | Deactivate a datasource (sets `is_active=false` without deleting)       |
| `GET`    | `/{id}/schema`             | Discover all accessible schema objects (namespaces → tables/views)      |
| `GET`    | `/{id}/tables`             | Paginated table list for `default_schema` (`offset`, `limit` params)    |
| `GET`    | `/{id}/search`             | Table name search within `default_schema` (`query` param)               |
| `GET`    | `/{id}/columns`            | Column metadata for a single table (`schema_name`, `table_name` params) |
| `POST`   | `/{id}/sync-relationships` | Trigger background FK discovery for a schema; returns 202 immediately   |
| `DELETE` | `/{id}`                    | Permanently delete a datasource and all associated annotation data      |

**Annotations** (`/api/v1/annotations`):

| Method   | Path                                    | Description                                          |
| -------- | --------------------------------------- | ---------------------------------------------------- |
| `GET`    | `/{id}/{schema}/{table}`                | Get table description + column annotations           |
| `PUT`    | `/{id}/{schema}/{table}`                | Save (upsert) table description + column annotations |
| `GET`    | `/{id}/{schema}/relationships`          | List all relationships for a schema                  |
| `POST`   | `/{id}/{schema}/relationships`          | Create a new relationship → 201                      |
| `DELETE` | `/{id}/{schema}/relationships/{rel_id}` | Delete a relationship → 204                          |

**Test result categories:**

| Category               | Meaning                                              |
| ---------------------- | ---------------------------------------------------- |
| `AUTH_FAILED`          | Wrong username/password/token                        |
| `HOST_UNREACHABLE`     | Network failure — can't reach the host               |
| `TLS_HANDSHAKE_FAILED` | Certificate or protocol mismatch                     |
| `TIMEOUT`              | Connection took longer than 10 seconds               |
| `UNSUPPORTED_CONFIG`   | e.g., Kerberos without Thick Mode installed          |
| `NETWORK_ERROR`        | InsightX server itself unreachable (frontend-synth.) |

### Data Flow

**Adding a new datasource:**

```
1. User picks engine → CredentialModal opens
2. User fills connection fields + default_schema
3. User picks auth method + fills credentials (or uploads wallet/keytab)
4. User configures TLS (mode, cert verify, optional cert file uploads)
5. Files uploaded → server returns paths → stored in modal state
6. POST /test called with resolved payload → SELECT 1 → {success, latency_ms}
7. On success: POST / called with same resolved payload
8. Backend encrypts credentials → writes to Metadata DB → returns DatasourceRecord
9. New entry appears in Connected Sources list
```

---

## M1 Feature Coverage

### User Stories

| Story     | Title                                                                                   | Status |
| --------- | --------------------------------------------------------------------------------------- | ------ |
| US 107147 | Database Connector Registration (Oracle, PostgreSQL, MS SQL Server)                     | ✅     |
| US 107148 | Authentication Configuration (Password, LDAP, Kerberos, Azure AD, Wallet, Windows Auth) | ✅     |
| US 107149 | TLS/SSL Encryption Configuration (mode, verify, CA cert, mTLS)                          | ✅     |
| US 107150 | Connection Test & Validation                                                            | ✅     |
| US 107151 | Permission-Scoped Object Browser (cards, pagination, search, row counts)                | ✅     |

### Acceptance Criteria

| Criterion                                  | Implementation                                                                   |
| ------------------------------------------ | -------------------------------------------------------------------------------- |
| At least 3 DB types supported              | PostgreSQL, Oracle 12c+, MS SQL Server                                           |
| Schema auto-scoped to `default_schema`     | Required at connection time; object browser always uses it                       |
| Tables shown as cards with metadata        | `TableCard` — type badge, column count, row count from system stats              |
| Pagination in object browser               | 10 cards/page; First/Prev/page-numbers/Next/Last                                 |
| Search tables within schema                | Backend `GET /{id}/search` — case-insensitive partial match                      |
| Sync refreshes table list                  | `Sync now` button re-fetches from the live database                              |
| Row counts without full table scan         | System statistics (pg_stat_user_tables / all_tables / sys.partitions)            |
| Invalid credentials show meaningful errors | `TestConnectionResult.category` + `message` per error type                       |
| Test is non-destructive                    | Only `SELECT 1` executed                                                         |
| TLS verify-cert warning when disabled      | Warning banner shown in modal when `Verify server certificate` is unchecked      |
| Delete connection                          | Trash button in Connected Sources with confirm dialog → `DELETE /{id}`           |
| Files uploaded once, reused on save        | Wallet/keytab/certs uploaded at test time; resolved paths passed through to save |
| Timeout threshold                          | 10 seconds per driver                                                            |

### Security

| Requirement                    | Implementation                                                                 |
| ------------------------------ | ------------------------------------------------------------------------------ |
| Credentials never in plaintext | AES-256-GCM (`credential_encryptor.py`)                                        |
| Multi-tenant isolation         | `tenant_id` on every row, filtered on every query                              |
| TLS files not in DB            | Server-side paths only; files stored in `SECURE_FILES_DIR`                     |
| Credentials stripped from API  | `DatasourceRecord` returns `has_credentials: bool` — never the raw credentials |
| Server cert verification flag  | `verify_server_cert` stored and sent per connection; UI warns when disabled    |
