// web/lib/types/datasource.ts
//
// PURPOSE:
//   Shared TypeScript type definitions for the datasource domain.
//   These mirror the Pydantic models in api/app/modules/datasources/schemas.py.
//   When the backend schema changes, update this file to match.

// ---------------------------------------------------------------------------
// Payload types (what we send TO the backend)
// ---------------------------------------------------------------------------

export type EngineType = "postgresql" | "oracle" | "mssql" | "delta";
export type AuthMethod = "password" | "ldap" | "wallet" | "kerberos" | "windows" | "azure_ad" | "none";
export type OracleConnectionType = "sid" | "service_name";

/** TLS configuration block sent in the datasource payload */
export interface TLSPayload {
  enabled:            boolean;
  verify_server_cert: boolean;
  mode:               string;
  ca_cert_path?:      string | null;
  client_cert_path?:  string | null;
  client_key_path?:   string | null;
}

/** Full datasource payload for POST / and POST /test */
export interface DatasourcePayload {
  name:                   string;
  engine:                 EngineType;
  host:                   string;
  port:                   number;
  database:               string;
  oracle_connection_type?: OracleConnectionType | null;
  default_schema:         string;
  auth_method:            AuthMethod;
  credentials:            Record<string, unknown>;
  tls:                    TLSPayload;
}

// ---------------------------------------------------------------------------
// Response types (what we receive FROM the backend)
// ---------------------------------------------------------------------------

/** Response from POST /test and POST /{id}/test */
export interface TestConnectionResult {
  success:    boolean;
  latency_ms: number;
  category?:  string;    // AUTH_FAILED | HOST_UNREACHABLE | TLS_HANDSHAKE_FAILED | etc.
  message?:   string;    // Human-readable explanation for failures
}

/** A single datasource record — sensitive fields stripped by the backend */
export interface DatasourceRecord {
  id:                     string;
  name:                   string;
  tenant_id:              string;
  engine:                 EngineType;
  host:                   string;
  port:                   number;
  database_name:          string;
  oracle_connection_type?: OracleConnectionType | null;
  auth_method:            AuthMethod;
  tls_enabled:            boolean;
  tls_mode?:              string | null;
  created_at:             string;
  updated_at:             string;
  created_by?:            string | null;
  last_tested_at?:        string | null;
  last_test_status?:      "success" | "failed" | null;
  default_schema:         string;
  is_active:              boolean;
  // Presence flags — indicate something is configured, not what it is
  has_credentials:        boolean;
  has_ca_cert:            boolean;
  has_client_cert:        boolean;
}

/** List response wrapper */
export interface DatasourceListResponse {
  data:  DatasourceRecord[];
  count: number;
}

/** Response from POST /upload */
export interface FileUploadResponse {
  path:     string;
  filename: string;
  type:     string;
}

// ---------------------------------------------------------------------------
// Schema discovery types (US 107151)
// ---------------------------------------------------------------------------

export interface SchemaObject {
  name:         string;
  type:         "TABLE" | "VIEW";
  column_count: number;
  row_count:    number;
}

export interface SchemaNamespace {
  name:   string;
  tables: SchemaObject[];
  views:  SchemaObject[];
}

export interface SchemaDiscoveryResult {
  datasource_id:    string;
  datasource_name:  string;
  engine:           EngineType;
  namespaces:       SchemaNamespace[];
  summary: {
    total_schemas: number;
    total_tables:  number;
    total_views:   number;
  };
}

/** Response from GET /{id}/tables — one paginated page of a single schema */
export interface TableBrowseResult {
  datasource_id:   string;
  datasource_name: string;
  engine:          EngineType;
  schema_name:     string;
  objects:         SchemaObject[];   // flat list: tables first, then views
  total_tables:    number;           // total across ALL pages for this schema
  total_views:     number;
  offset:          number;
  limit:           number;
  has_more:        boolean;
}

/** Response from GET /{id}/search — search results for table names */
export interface SearchTableResult {
  datasource_id:   string;
  datasource_name: string;
  engine:          EngineType;
  schema_name:     string;
  objects:         SchemaObject[];
  total:           number;
}

// ---------------------------------------------------------------------------
// Form state types (internal — used by hooks)
// ---------------------------------------------------------------------------

export interface ConnectionState {
  name:                 string;
  host:                 string;
  port:                 number | string;
  database:             string;
  oracleConnectionType: OracleConnectionType;
}

export interface AuthState {
  method:      AuthMethod | "";
  credentials: Record<string, unknown>;
}

export interface TLSState {
  enabled:          boolean;
  verifyServerCert: boolean;
  mode:             string;
  // File objects (before upload) — not JSON-serializable
  caCert?:          File | null;
  clientCert?:      File | null;
  clientKey?:       File | null;
  // Server-side paths (after upload) — embedded in the payload
  caCertPath?:      string | null;
  clientCertPath?:  string | null;
  clientKeyPath?:   string | null;
}
