# api/app/modules/datasources/schemas.py
#
# PURPOSE:
#   Pydantic v2 models for all datasource request and response shapes.
#   FastAPI uses these to:
#     - Parse and validate incoming request bodies (HTTP 422 on failure)
#     - Serialize outgoing responses
#     - Auto-generate OpenAPI documentation (/docs, /redoc)
#
# CROSS-FIELD VALIDATION:
#   model_validator(mode='after') runs AFTER individual field validators.
#   Rules enforced:
#     1. oracle_connection_type is required for Oracle, forbidden for others
#     2. auth_method must be valid for the selected engine
#     3. credentials must match the auth_method discriminated union shape

from typing import Annotated, Any, Literal, Optional, Union
from enum import Enum

from pydantic import BaseModel, Field, model_validator, ConfigDict

from app.core.engines_config import ENGINES


# =============================================================================
# Enums
# =============================================================================

class EngineType(str, Enum):
    """Supported database engine identifiers."""
    postgresql = "postgresql"
    mssql      = "mssql"
    oracle     = "oracle"
    delta      = "delta"


class AuthMethod(str, Enum):
    """All supported authentication methods across all engines."""
    password = "password"   # All engines
    ldap     = "ldap"       # PostgreSQL
    wallet   = "wallet"     # Oracle
    kerberos = "kerberos"   # Oracle (Thick Mode required)
    windows  = "windows"    # MSSQL
    azure_ad = "azure_ad"   # MSSQL
    none     = "none"       # Delta (open Spark standalone cluster)


class OracleConnectionType(str, Enum):
    """Oracle-specific: how the database is identified."""
    sid          = "sid"           # Legacy, for Oracle 11g and older setups
    service_name = "service_name"  # Recommended for Oracle 12c+


# =============================================================================
# TLS Sub-model
# =============================================================================

class TLSConfig(BaseModel):
    """
    TLS/SSL configuration block.
    File paths here are server-side paths returned by POST /upload.
    They are NEVER sent back to the client in response bodies.
    """
    enabled:            bool = False
    verify_server_cert: bool = True   # Default ON — secure by default

    # Engine-aware TLS mode:
    #   PostgreSQL: 'require' | 'verify-ca' | 'verify-full' | 'disable' etc.
    #   MSSQL:      'encrypt'
    #   Oracle:     'ssl'
    mode: Optional[str] = None

    # Server-side file paths — stored in DB, never returned to client
    ca_cert_path:     Optional[str] = None
    client_cert_path: Optional[str] = None
    client_key_path:  Optional[str] = None

    @model_validator(mode="after")
    def validate_tls(self) -> "TLSConfig":
        """Ensures mode is provided when TLS is enabled."""
        if self.enabled and not self.mode:
            raise ValueError("tls.mode is required when tls.enabled is true")
        return self


# =============================================================================
# Credential Models
# =============================================================================

class PasswordCredentials(BaseModel):
    method: Literal["password"]
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LdapCredentials(BaseModel):
    method: Literal["ldap"]
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class WalletCredentials(BaseModel):
    method: Literal["wallet"]
    wallet_location: str = Field(min_length=1)
    wallet_password: Optional[str] = None
    username: Optional[str] = None


class KerberosCredentials(BaseModel):
    method: Literal["kerberos"]
    principal: str = Field(min_length=1)
    keytab_path: str = Field(min_length=1)


class WindowsCredentials(BaseModel):
    method: Literal["windows"]
    domain: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None


class AzureAdCredentials(BaseModel):
    method: Literal["azure_ad"]
    access_token: str = Field(min_length=1)


class SparkCredentials(BaseModel):
    """
    Delta Lakehouse — open Spark standalone cluster, no username/password.
    hdfs_namenode/warehouse_dir describe the target cluster (not this backend
    machine's local Spark/Java install, which is configured via api/.env).
    """
    method: Literal["none"]
    hdfs_namenode: str = Field(min_length=1, description="e.g. hdfs://10.11.204.203:9000")
    warehouse_dir: Optional[str] = Field(
        default=None, description="Defaults to '<hdfs_namenode>/user/spark/warehouse' if omitted"
    )


Credentials = Annotated[
    Union[
        PasswordCredentials,
        LdapCredentials,
        WalletCredentials,
        KerberosCredentials,
        WindowsCredentials,
        AzureAdCredentials,
        SparkCredentials,
    ],
    Field(discriminator="method"),
]


# =============================================================================
# Request Models
# =============================================================================

class DatasourcePayload(BaseModel):
    """
    Input model for:
      POST /api/v1/datasources        (create and save)
      POST /api/v1/datasources/test   (test without saving)

    The test endpoint uses the same payload shape — it just doesn't persist anything.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name":                   "Finance Oracle Prod",
                "engine":                 "oracle",
                "host":                   "oracle.company.com",
                "port":                   1521,
                "database":               "ORCL",
                "oracle_connection_type": "service_name",
                "default_schema":         "FINANCE",
                "auth_method":            "password",
                "credentials":            {"username": "analytics_user", "password": "secret"},
                "tls":                    {"enabled": True, "mode": "ssl", "verify_server_cert": True},
            }
        }
    )

    name:     str       = Field(min_length=1, max_length=100, description="Human-readable connection name")
    engine:   EngineType
    host:     str       = Field(min_length=1, description="Hostname or IP address")
    port:     int       = Field(ge=1, le=65535, description="Database port number")
    database: str       = Field(min_length=1, description="DB name, SID, or Service Name")

    # Oracle-only — required when engine='oracle', must be absent for other engines
    oracle_connection_type: Optional[OracleConnectionType] = None

    # The schema to browse in the object browser — mandatory, user cannot change it after save
    default_schema: str = Field(min_length=1, description="Schema/owner name to browse in the object browser")

    auth_method:  AuthMethod
    credentials:  Credentials
    tls:          Optional[TLSConfig] = None

    @model_validator(mode="before")
    @classmethod
    def inject_credential_method(cls, data: Any) -> Any:
        """
        Preserve the public API shape while enabling a discriminated union.
        Clients send auth_method separately; credentials.method is filled in
        before Pydantic validates the credential subtype.
        """
        if not isinstance(data, dict):
            return data

        auth_method = data.get("auth_method")
        credentials = data.get("credentials")
        if auth_method and isinstance(credentials, dict) and "method" not in credentials:
            data = {**data, "credentials": {**credentials, "method": auth_method}}

        return data

    @model_validator(mode="after")
    def validate_engine_and_auth(self) -> "DatasourcePayload":
        """
        Runs after all individual field validators succeed.
        Raises ValueError (→ HTTP 422) for rule violations.
        """
        # --- Rule 1: oracle_connection_type ---
        if self.engine == EngineType.oracle:
            if self.oracle_connection_type is None:
                raise ValueError(
                    "oracle_connection_type ('sid' or 'service_name') is required when engine is 'oracle'"
                )
        else:
            if self.oracle_connection_type is not None:
                raise ValueError(
                    "oracle_connection_type must not be set for non-Oracle engines"
                )

        # --- Rule 2: auth_method compatibility ---
        valid_methods = ENGINES[self.engine.value]["supported_auth_methods"]
        if self.auth_method.value not in valid_methods:
            raise ValueError(
                f"'{self.auth_method.value}' is not a valid auth method for '{self.engine.value}'. "
                f"Valid methods: {valid_methods}"
            )

        # --- Rule 3: credentials discriminator must match auth_method ---
        if self.credentials.method != self.auth_method.value:
            raise ValueError("credentials.method must match auth_method")

        return self


# =============================================================================
# Response Models
# =============================================================================

class TestConnectionResponse(BaseModel):
    """
    Response from POST /datasources/test and POST /datasources/{id}/test.
    Always HTTP 200 — a failed DB connection is NOT an HTTP error.
    success/failure is encoded in the body.
    """
    success:    bool
    latency_ms: int
    category:   Optional[str] = None   # AUTH_FAILED | HOST_UNREACHABLE | TLS_HANDSHAKE_FAILED | etc.
    message:    Optional[str] = None   # Human-readable explanation of failure


class DatasourceResponse(BaseModel):
    """
    Safe datasource record for API responses.
    Sensitive fields are completely stripped.
    Boolean presence flags indicate what is configured without revealing values.
    """
    id:                     str
    name:                   str
    tenant_id:              str
    engine:                 str
    host:                   str
    port:                   int
    database_name:          str
    oracle_connection_type: Optional[str] = None
    auth_method:            str
    tls_enabled:            bool
    tls_mode:               Optional[str] = None
    created_at:             str
    updated_at:             str
    created_by:             Optional[str] = None
    last_tested_at:         Optional[str] = None
    last_test_status:       Optional[str] = None

    default_schema: Optional[str] = None

    is_active: bool = True

    # Presence flags — indicate "something is configured" without revealing what
    has_credentials: bool = False
    has_ca_cert:     bool = False
    has_client_cert: bool = False


class DatasourceListResponse(BaseModel):
    data:  list[DatasourceResponse]
    count: int


class FileUploadResponse(BaseModel):
    """Response from POST /datasources/upload."""
    path:     str   # Server-side absolute path — embed in datasource payload
    filename: str
    type:     str   # 'ca_cert' | 'client_cert' | 'client_key' | 'wallet' | 'keytab'


# =============================================================================
# Schema Discovery Response (US 107151)
# =============================================================================

class SchemaObject(BaseModel):
    """A single table or view in the schema."""
    name:         str
    type:         str             # 'TABLE' or 'VIEW'
    column_count: int = 0
    row_count:    int = 0          # Approximate count from system tables (not COUNT*)


class SchemaNamespace(BaseModel):
    """A single schema/namespace containing tables and views."""
    name:    str
    tables:  list[SchemaObject] = []
    views:   list[SchemaObject] = []


class SchemaDiscoveryResponse(BaseModel):
    """
    Response from GET /datasources/{id}/schema.
    Returns the schema objects visible to the datasource's authenticated user.
    """
    datasource_id:  str
    datasource_name: str
    engine:         str
    namespaces:     list[SchemaNamespace]
    summary: dict = {}   # { total_schemas, total_tables, total_views, total_sequences }


# =============================================================================
# Paginated Table Browser Response (new — replaces full schema discovery for
# the object browser, one schema at a time with pagination)
# =============================================================================

class TableBrowseResponse(BaseModel):
    """
    Response from GET /datasources/{id}/tables?schema_name=X&offset=0&limit=10.
    Returns a single page of tables/views for one schema.
    """
    datasource_id:   str
    datasource_name: str
    engine:          str
    schema_name:     str
    objects:         list[SchemaObject]   # Flat list: tables + views for this page
    total_tables:    int                  # Total tables in this schema (all pages)
    total_views:     int                  # Total views in this schema (all pages)
    offset:          int
    limit:           int
    has_more:        bool


class SearchTableResponse(BaseModel):
    """
    Response from GET /datasources/{id}/search?schema_name=X&query=Y.
    Returns all matching tables/views for a schema (no pagination needed for search).
    """
    datasource_id:   str
    datasource_name: str
    engine:          str
    schema_name:     str
    objects:         list[SchemaObject]
    total:           int


class ColumnMetaResponse(BaseModel):
    """Single column's metadata, returned as part of ColumnMetaListResponse."""
    name:           str
    type:           str
    nullable:       bool
    is_primary_key: bool
    is_foreign_key: bool
    fk_table:       Optional[str] = None
    fk_column:      Optional[str] = None


class ColumnMetaListResponse(BaseModel):
    """Response from GET /datasources/{id}/columns?schema_name=X&table_name=Y."""
    datasource_id:   str
    datasource_name: str
    engine:          str
    schema_name:     str
    table_name:      str
    columns:         list[ColumnMetaResponse]
