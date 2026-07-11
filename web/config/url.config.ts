// web/config/url.config.ts
//
// PURPOSE:
//   The single source of truth for every backend URL the web app talks to.
//   Hostnames live ONLY here (read from env); no other file may hardcode a host
//   or read process.env for an API URL.
//
// CONVENTIONS:
//   - Static endpoints  → string constants.
//   - Parameterized URLs → functions returning a string.
//   - Names are verb-prefixed after the HTTP method (get… / post… / put… / delete…).
//
// Adding an endpoint = add ONE entry here, then a typed hook call. Nothing else.

/**
 * Reads a required NEXT_PUBLIC_ env var, throwing a clear error if it is missing.
 * `process.env.NEXT_PUBLIC_*` is statically inlined by Next.js at build time, so
 * this runs once when the module is first imported.
 */
function requireEnv(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(
      `Missing required environment variable ${name}. ` +
        `Add it to web/.env (see web/.env.example).`,
    );
  }
  // Strip a trailing slash so endpoint templates can always start with "/".
  return value.replace(/\/+$/, "");
}

export const baseURL = requireEnv(
  "NEXT_PUBLIC_BASE_URL",
  process.env.NEXT_PUBLIC_BASE_URL,
);

console.log(`Using backend base URL: ${baseURL}`);
// ---------------------------------------------------------------------------
// Auth BFF — browser redirects and identity endpoints
// All served by the backend; tokens live in HttpOnly cookies only.
// ---------------------------------------------------------------------------

/** GET — redirects browser to Keycloak login (PKCE S256). */
export const getAuthLogin = `${baseURL}/api/auth/login`;

/** GET — returns the current user's identity decoded from token cookies. */
export const getAuthMe = `${baseURL}/api/auth/me`;

/** GET — clears auth cookies and redirects browser to /login. */
export const getAuthLogout = `${baseURL}/api/auth/logout`;

/** POST — exchanges the refresh_token cookie for a fresh access_token. */
export const postAuthRefresh = `${baseURL}/api/auth/refresh`;

// ---------------------------------------------------------------------------
// Datasources (Module 1) — every endpoint under /api/v1/datasources
// ---------------------------------------------------------------------------

/** GET list / POST create share the collection URL — named per HTTP verb. */
export const getDatasources = `${baseURL}/api/v1/datasources`;
export const postDatasource = `${baseURL}/api/v1/datasources`;

/** POST pre-save connection test (plaintext creds, not persisted). */
export const postDatasourceTest = `${baseURL}/api/v1/datasources/test`;

/** POST upload a TLS cert / Oracle Wallet / Kerberos keytab (multipart). */
export const postDatasourceUpload = `${baseURL}/api/v1/datasources/upload`;

/** POST re-test a saved datasource using its stored credentials. */
export const postDatasourceRetest = (id: string) =>
  `${baseURL}/api/v1/datasources/${id}/test`;

/** GET discover schema objects for a saved datasource. */
export const getDatasourceSchema = (id: string) =>
  `${baseURL}/api/v1/datasources/${id}/schema`;

/** GET one paginated page of tables/views for a single schema. */
export const getDatasourceTables = (
  id: string,
  schemaName: string,
  offset = 0,
  limit = 10,
) =>
  `${baseURL}/api/v1/datasources/${id}/tables` +
  `?schema_name=${encodeURIComponent(schemaName)}&offset=${offset}&limit=${limit}`;

/** GET search tables/views by name within a schema. */
export const getDatasourceSearch = (
  id: string,
  schemaName: string,
  query: string,
) =>
  `${baseURL}/api/v1/datasources/${id}/search` +
  `?schema_name=${encodeURIComponent(schemaName)}&query=${encodeURIComponent(query)}`;

/** PATCH deactivate a saved datasource (sets is_active=false without deleting). */
export const patchDatasourceDeactivate = (id: string) =>
  `${baseURL}/api/v1/datasources/${id}/deactivate`;

/** DELETE a saved datasource and its encrypted credentials. */
export const deleteDatasourceUrl = (id: string) =>
  `${baseURL}/api/v1/datasources/${id}`;

// ---------------------------------------------------------------------------
// Annotations (Module 2) — every endpoint under /api/v1/annotations
// ---------------------------------------------------------------------------

/** POST trigger background FK relationship discovery for a schema (returns 202). */
export const postSyncRelationships = (id: string, schemaName: string) =>
  `${baseURL}/api/v1/datasources/${id}/sync-relationships?schema_name=${encodeURIComponent(schemaName)}`;

/** GET column metadata (names, types, PK/FK flags) for a single table. */
export const getDatasourceColumns = (
  id: string,
  schemaName: string,
  tableName: string,
) =>
  `${baseURL}/api/v1/datasources/${id}/columns` +
  `?schema_name=${encodeURIComponent(schemaName)}&table_name=${encodeURIComponent(tableName)}`;

/** GET existing annotations for a table (description + per-column text). */
export const getTableAnnotations = (
  id: string,
  schemaName: string,
  tableName: string,
) =>
  `${baseURL}/api/v1/annotations/${id}/${encodeURIComponent(schemaName)}/${encodeURIComponent(tableName)}`;

/** PUT save annotations for a table (upserts description + column annotations). */
export const putTableAnnotations = (
  id: string,
  schemaName: string,
  tableName: string,
) =>
  `${baseURL}/api/v1/annotations/${id}/${encodeURIComponent(schemaName)}/${encodeURIComponent(tableName)}`;

/** GET all relationships defined for a schema. */
export const getSchemaRelationships = (id: string, schemaName: string) =>
  `${baseURL}/api/v1/annotations/${id}/${encodeURIComponent(schemaName)}/relationships`;

/** POST create a new relationship within a schema. */
export const postSchemaRelationship = (id: string, schemaName: string) =>
  `${baseURL}/api/v1/annotations/${id}/${encodeURIComponent(schemaName)}/relationships`;

/** DELETE a specific relationship by id. */
export const deleteSchemaRelationship = (
  id: string,
  schemaName: string,
  relId: string,
) =>
  `${baseURL}/api/v1/annotations/${id}/${encodeURIComponent(schemaName)}/relationships/${relId}`;

// ---------------------------------------------------------------------------
// NL-to-SQL (M3) — natural language querying
// ---------------------------------------------------------------------------
/**
 * POST run a full NL-to-SQL pipeline (generate + execute) in one request.
 * Body: { schema_name: string, question: string }
 * Response: NLQueryResponse (sql, columns, rows, narrative, ...)
 */
export const postNLQuery = (datasourceId: string) =>
  `${baseURL}/api/v1/nl-query/${datasourceId}/query`;

/**
 * POST generate SQL from a question WITHOUT executing it (preview step).
 * Body: { schema_name: string, question: string }
 * Response: GenerateSQLResponse (query_id, sql, tables_used, ...)
 */
export const postGenerateSQL = (datasourceId: string) =>
  `${baseURL}/api/v1/nl-query/${datasourceId}/generate`;

/**
 * POST execute a previously-generated (and optionally user-edited) SQL.
 * Body: { query_id: string, sql: string }
 * Response: ExecuteSQLResponse (columns, rows, row_count, exec_ms, narrative)
 */
export const postExecuteSQL = (datasourceId: string) =>
  `${baseURL}/api/v1/nl-query/${datasourceId}/execute`;

/**
 * POST build pgvector embeddings + AGE graph for a schema.
 * Body: { schema_name: string }
 * Must be called once per schema before /query will work.
 * Response: IndexSchemaResponse (indexed_tables, age_graph)
 */
export const postIndexSchema = (datasourceId: string) =>
  `${baseURL}/api/v1/nl-query/${datasourceId}/index`;

/**
 * GET list recent NL query history for a datasource.
 * Query params: limit (1-100, default 20)
 */
export const getNLQueryHistory = (datasourceId: string, limit = 20) =>
  `${baseURL}/api/v1/nl-query/${datasourceId}/history?limit=${limit}`;

/**
 * POST record thumbs-up/thumbs-down on a query result.
 * Body: { is_correct: boolean }
 */
export const postNLQueryFeedback = (datasourceId: string, queryId: string) =>
  `${baseURL}/api/v1/nl-query/${datasourceId}/feedback/${queryId}`;

/**
 * GET Ollama health check — shows which models are available.
 * No auth required.
 */
export const getNLQueryHealth = () => `${baseURL}/api/v1/nl-query/health`;
