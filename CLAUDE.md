# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

AI assistant guide for the InsightX repository. Read this before touching any file.

## What Is InsightX

InsightX is a full-stack agentic reporting platform. Users register enterprise databases,
browse permission-scoped schema metadata, and later query data in natural language to build
shareable insights.

Heavily comment the generated code for explainability.

Only `web/` and `api/` are active today. `infra/`, `job/`, and `mcp/` are placeholders.

---

## Dev Commands

### Frontend (`web/`)

```bash
cd web
npm install
npm run dev       # :8091, hot reload
npm run build     # type-check + compile
npm run lint      # ESLint with Next.js/TS configs
```

### Frontend `web/.env` (required before first run)

```
NEXT_PUBLIC_BASE_URL=http://localhost:8091
```

This is the only required frontend env var. It is read **only** in `web/config/url.config.ts`. All auth and API endpoints are derived from it — no Keycloak env vars are needed on the frontend (the backend handles all Keycloak communication).

### Backend (`api/`)

```bash
cd api
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
cp .env.example .env            # fill in values
uvicorn app.main:app --reload --port 8000
```

Interactive API docs: http://localhost:8000/docs

### Backend `.env`

`CREDENTIAL_ENCRYPTION_KEY` is required. `DATABASE_URL` defaults to PostgreSQL in code;
SQLite is only an explicit local/test fallback.

```env
DATABASE_URL=postgresql+asyncpg://insightx:your_password@localhost:5432/insightx_meta
CREDENTIAL_ENCRYPTION_KEY=<64 hex chars — generate: python -c "import secrets; print(secrets.token_hex(32))">
SECURE_FILES_DIR=./secure-uploads
MAX_UPLOAD_SIZE_MB=5

DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=20

# Leave KEYCLOAK_URL empty for dev mode (no Keycloak required — hardcoded dev user is returned)
KEYCLOAK_URL=
KEYCLOAK_REALM=InsightX
KEYCLOAK_CLIENT_ID=InsightX
KEYCLOAK_CLIENT_SECRET=

# BFF OAuth redirect — must be registered in Keycloak as a valid Redirect URI
REDIRECT_URI=http://localhost:8091/api/auth/callback
FRONTEND_URL=http://localhost:8091

INTROSPECT_CACHE_TTL_SECONDS=30

# M3 NL-to-SQL — Ollama local LLM (must be running and models pulled before using M3)
# Pull: ollama pull nomic-embed-text && ollama pull sqlcoder:7b && ollama pull llama3.1:8b
OLLAMA_BASE_URL=http://10.11.200.99:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
OLLAMA_SQL_MODEL=sqlcoder:7b
OLLAMA_NARRATIVE_MODEL=llama3.1:8b
OLLAMA_TIMEOUT_SECONDS=120
```

---

## Architecture

```
Browser (Next.js :8091)
  └─ /api/* rewritten → FastAPI :8000 (next.config.ts proxy)
        ├─ /api/auth/*           BFF OAuth (Keycloak PKCE S256 flow, HttpOnly cookies)
        ├─ /api/v1/datasources   M1 — register + test database connections
        ├─ /api/v1/annotations   M2 — table/column annotations + FK relationships
        ├─ /api/v1/nl-query      M3 — NL-to-SQL via local Ollama LLM
        ├─ /api/v1/chat          M3 — SSE streaming chat sessions
        ├─ Metadata DB (PostgreSQL — datasources, annotations, query history, conversations)
        └─ Target DBs (runtime connections from user registrations)
              ├─ PostgreSQL via asyncpg
              ├─ Oracle via python-oracledb
              └─ MSSQL via pyodbc
```

**Two distinct database concepts — never confuse them:**

|                | Metadata DB                                       | Target Datasource              |
| -------------- | ------------------------------------------------- | ------------------------------ |
| Purpose        | Stores registrations, annotations, chat history   | The user's Oracle / PG / MSSQL |
| How configured | `DATABASE_URL` env var                            | Runtime user input             |
| Sensitive?     | No                                                | Yes — encrypted at rest        |

---

## Frontend (`web/`)

**Stack:** Next.js 16 (App Router) · React 19 · TypeScript 5 · Tailwind CSS 4 · Redux Toolkit

**Important:** This is Next.js 16, which has breaking changes from earlier versions. Read `node_modules/next/dist/docs/` before writing any Next.js-specific code. This is a hard rule from `web/AGENTS.md`.

**Key patterns:**

- All routes live under `web/app/` (App Router — not Pages Router).
- **URL config** — every backend endpoint string lives in `web/config/url.config.ts`. Never hardcode a host or read `process.env` for an API URL anywhere else. Only `NEXT_PUBLIC_BASE_URL` is required.
- **HTTP layer** — all API calls go through `web/lib/utils/fetch.utils.ts` (`get`, `post`, `put`, `del`, `patch`). It wraps `authFetch` from `web/lib/utils/auth-fetch.utils.ts`, which adds `credentials: 'include'` (sends HttpOnly auth cookies) and redirects to `/api/auth/login` on 401.
- **TypeScript types** live in `web/lib/types/interface/features/` (e.g. `datasource.interface.ts`, `auth.interface.ts`). These mirror the backend Pydantic models — keep them in sync when changing schemas.
- **Engine metadata** (`web/config/engines.ts`) is mirrored in `api/app/config/engines_config.py` — update **both** when adding a new engine.

---

## Backend (`api/`)

**Stack:** FastAPI · SQLAlchemy 2.0 async ORM · Pydantic v2 · asyncpg · python-oracledb · pyodbc · Cryptography (AES-256-GCM) · httpx · PyJWT

**Feature-module layout** — every module owns its own slice:

```
api/app/modules/<module>/
  router.py      ← FastAPI APIRouter (HTTP only, no logic)
  schemas.py     ← Pydantic v2 request/response models
  service.py     ← all business logic
  drivers/       ← per-engine adapters (datasources only)
```

**Authentication:** `get_current_user()` in `api/app/core/security.py` resolves the caller from the `access_token` HttpOnly cookie (BFF flow) or an `Authorization: Bearer` header (service-to-service). When `KEYCLOAK_URL` is empty it returns a hardcoded dev user so all endpoints work locally without Keycloak.

**Authorization:** `require_role(role)` in `api/app/core/guards.py` is a dependency factory. `insightx-admin` (realm role) bypasses all checks; other callers must hold the matching client role. Wrap any endpoint: `Depends(require_role("feat:datasource:create"))`.

**Credential security:** credentials are encrypted with AES-256-GCM in `credential_encryptor.py` before any DB write, and decrypted only at connection-test time. The format stored in the DB is `iv:tag:ciphertext` (all hex).

**Startup:** `redirect_slashes=False` is set on the FastAPI app — trailing-slash mismatches return 404 instead of 307. Routes that must accept both forms register dual-path decorators (`""` and `"/"`).

---

## Auth — BFF Authorization Code + PKCE Flow

The backend drives the full OAuth dance so JavaScript never touches raw tokens.

**Endpoints** (mounted at `/api/auth`, no version prefix):

| Method | Path        | Purpose                                                               |
| ------ | ----------- | --------------------------------------------------------------------- |
| GET    | `/login`    | Redirect browser to Keycloak (generates state + PKCE S256 cookies)    |
| GET    | `/callback` | Exchange code + PKCE verifier for tokens; set HttpOnly cookies        |
| GET    | `/me`       | Return caller identity decoded from cookies                           |
| POST   | `/refresh`  | Rotate access_token using refresh_token cookie                        |
| GET    | `/logout`   | Revoke Keycloak session, clear all auth cookies, redirect to `/login` |

Key files: `api/app/modules/auth/router.py`, `api/app/core/security.py`, `api/app/core/guards.py`, `api/app/core/config.py`, `web/app/component/AuthProvider.tsx`, `web/lib/utils/auth-fetch.utils.ts`.

**Dev mode:** set `KEYCLOAK_URL=` (empty) — `/login` skips Keycloak and sets a placeholder cookie; `/me` returns a hardcoded dev identity; `require_role` passes all requests through.

**Cookie path scoping:** `oauth_state` and `pkce_verifier` are scoped to `path="/api/auth"` so they are not sent with every API request — only to `/api/auth/callback` where they are needed.

**Keycloak registration requirement:** `REDIRECT_URI` must be added to the Keycloak client's valid Redirect URIs. Default dev value: `http://localhost:8091/api/auth/callback`.

---

## Module 1 — Datasource Onboarding (complete)

Six REST endpoints under `/api/v1/datasources`:

| Method | Path           | Purpose                                                   |
| ------ | -------------- | --------------------------------------------------------- |
| POST   | `/test`        | Pre-save connection test (plaintext creds, not persisted) |
| POST   | `/upload`      | Upload TLS cert / Oracle Wallet / Kerberos keytab         |
| POST   | `/`            | Create and persist datasource (encrypts creds)            |
| GET    | `/`            | List all datasources (creds stripped from response)       |
| POST   | `/{id}/test`   | Re-test a saved datasource (decrypts creds at runtime)    |
| GET    | `/{id}/schema` | Discover accessible schema objects                        |

The frontend is a single consolidated page at `web/app/datasource/page.tsx` using SWR.

---

## Module 2 — Annotations (complete)

Five REST endpoints under `/api/v1/annotations`:

| Method | Path                                       | Purpose                                              |
| ------ | ------------------------------------------ | ---------------------------------------------------- |
| GET    | `/{ds_id}/{schema}/{table}`                | Get table + column annotations                       |
| PUT    | `/{ds_id}/{schema}/{table}`                | Save annotations (triggers background M3 re-embed)   |
| GET    | `/{ds_id}/{schema}/relationships`          | List FK relationships for a schema                   |
| POST   | `/{ds_id}/{schema}/relationships`          | Create a relationship                                |
| DELETE | `/{ds_id}/{schema}/relationships/{rel_id}` | Delete a relationship                                |

**Route order matters:** `/relationships` routes are registered before `/{table_name}` routes in the router to prevent FastAPI matching the literal string "relationships" as a table name.

**Auto-reindex:** `PUT` on a table annotation fires a `BackgroundTask` after the 200 response that re-embeds only that table into the M3 pgvector index. It uses `AsyncSessionLocal()` directly because the request DB session closes before background tasks execute.

---

## Module 3 — NL-to-SQL + Chat (complete)

### NL-to-SQL  (`/api/v1/nl-query`)

| Method | Path                      | Purpose                                                  |
| ------ | ------------------------- | -------------------------------------------------------- |
| POST   | `/{ds_id}/query`          | Combined generate + execute (main flow)                  |
| POST   | `/{ds_id}/generate`       | Generate SQL preview only (no execution)                 |
| POST   | `/{ds_id}/execute`        | Execute a previously-generated (optionally edited) SQL   |
| POST   | `/{ds_id}/index`          | Build pgvector embeddings + Apache AGE graph for schema  |
| GET    | `/{ds_id}/history`        | List recent NL queries                                   |
| POST   | `/{ds_id}/feedback/{qid}` | Record thumbs-up/down on a result                        |
| GET    | `/health`                 | Check Ollama connectivity and model availability         |

**Pipeline:** question → pgvector semantic table selection (M2 annotations) → Ollama LLM SQL generation → sqlglot validation → target DB execution → Ollama narrative.

**Prerequisite:** run `POST /{ds_id}/index` at least once per schema before using `/query`. Re-run after bulk annotation changes (single-table saves auto-reindex via M2 background task).

**Ollama models required** (pull before using M3):
```bash
ollama pull nomic-embed-text   # embeddings
ollama pull sqlcoder:7b        # SQL generation
ollama pull llama3.1:8b        # narrative generation
```

### Chat Sessions  (`/api/v1/chat`)

| Method | Path                          | Purpose                                   |
| ------ | ----------------------------- | ----------------------------------------- |
| POST   | `/conversations`              | Create a new chat session                 |
| GET    | `/conversations`              | List the caller's conversations           |
| GET    | `/conversations/{id}`         | Fetch conversation + all messages         |
| DELETE | `/conversations/{id}`         | Delete a conversation                     |
| POST   | `/conversations/{id}/messages`| Send a message — **SSE stream response**  |

The message endpoint uses `StreamingResponse` with `media_type="text/event-stream"`. The frontend reads with `fetch()` + `ReadableStream` (not `EventSource`) because a JSON body is required. Include `X-Accel-Buffering: no` when behind Nginx to prevent response buffering.

---

## Adding a New Module (M4+)

1. Create `api/app/modules/<name>/` with `router.py`, `schemas.py`, `service.py`.
2. Register the router in `api/app/main.py`.
3. Add a migration under `api/database/migrations/`.
4. Add frontend types in `web/lib/types/interface/features/<name>.interface.ts`, add all endpoint URLs to `web/config/url.config.ts`, and create the route under `web/app/<name>/`. Use `get`/`post`/`del` from `web/lib/utils/fetch.utils.ts` for all API calls.
