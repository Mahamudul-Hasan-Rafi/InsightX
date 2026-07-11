# api/app/modules/datasources/service.py
#
# PURPOSE:
#   Business logic layer. No HTTP awareness — no Request/Response objects.
#   Called by the router; calls the encryptor, tester, and ORM.
#
# PUBLIC FUNCTIONS:
#   test_datasource_connection()   — test before save (plaintext credentials from form)
#   create_datasource()            — encrypt + persist a new datasource
#   list_datasources()             — tenant-scoped list, credentials always stripped
#   retest_saved_datasource()      — re-test a SAVED datasource using stored credentials
#   get_datasource_schema()        — discover schema objects for a saved datasource
#
# WHY retest_saved_datasource() IS IMPORTANT:
#   The /test endpoint tests BEFORE saving (credentials come from the form in plaintext).
#   After saving, the user may want to re-test the connection (e.g., after a DB restart,
#   or from the list page) WITHOUT re-entering credentials.
#   retest_saved_datasource() loads the saved record, decrypts credentials, and re-tests.
#   This is also the function called after an app restart — the saved credentials
#   survive restarts because they are stored encrypted in the metadata DB.

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.datasource import Datasource
from app.modules.datasources.credential_encryptor import encrypt, decrypt
from app.modules.datasources.connection_tester import test_connection
from app.modules.datasources.schema_inspector import discover_schema, browse_schema_tables as _browse_tables, search_schema_tables as _search_tables, inspect_table_columns as _inspect_columns
from app.modules.datasources.schemas import DatasourcePayload


# =============================================================================
# Exceptions
# =============================================================================

class DatasourceInactiveError(ValueError):
    """
    Raised when a runtime operation is attempted on a deactivated datasource.
    The single raise site is _datasource_runtime_config — every caller inherits
    this check automatically without needing explicit guards.
    """


# =============================================================================
# Public Service Functions
# =============================================================================

async def test_datasource_connection(payload: DatasourcePayload) -> dict:
    """
    Tests a connection WITHOUT saving anything.
    Called by POST /test — credentials come from the wizard form in plaintext.

    This is the "pre-save test": the user has filled the form but hasn't saved yet.
    We test the connection to give immediate feedback, then they can save.

    Args:
        payload: Validated DatasourcePayload with plaintext credentials

    Returns:
        Connection test result dict from connection_tester.test_connection()
    """
    # Convert Pydantic model to plain dict for the driver layer
    config = payload.model_dump()
    return await test_connection(config)


async def create_datasource(
    payload:   DatasourcePayload,
    tenant_id: str,
    user_id:   str,
    db:        AsyncSession,
) -> dict:
    """
    Creates and persists a new datasource record.

    The plaintext credentials are ENCRYPTED before the DB write.
    The raw credentials dict never touches the datasources table.

    Args:
        payload:   Validated DatasourcePayload (credentials in plaintext)
        tenant_id: From the authenticated session — never from the request body
        user_id:   For the created_by audit field
        db:        Injected AsyncSession from get_db()

    Returns:
        Safe datasource dict (all sensitive fields stripped)

    Raises:
        ValueError: If a datasource with this name already exists for the tenant
    """
    # Check for duplicate name within the tenant first.
    # The DB UNIQUE constraint also enforces this, but this gives a friendlier message.
    existing = await db.execute(
        select(Datasource).where(
            Datasource.tenant_id == tenant_id,
            Datasource.name      == payload.name,
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError(f"A data source named '{payload.name}' already exists for this tenant.")

    # Encrypt credentials — the ONLY place encryption happens for new datasources
    encrypted_creds = encrypt(payload.credentials.model_dump())

    tls = payload.tls

    datasource = Datasource(
        id                    = uuid.uuid4(),
        name                  = payload.name,
        tenant_id             = tenant_id,
        engine                = payload.engine.value,
        host                  = payload.host,
        port                  = payload.port,
        database_name         = payload.database,
        oracle_connection_type= payload.oracle_connection_type.value if payload.oracle_connection_type else None,
        auth_method           = payload.auth_method.value,
        encrypted_credentials = encrypted_creds,
        tls_enabled           = tls.enabled            if tls else False,
        tls_verify_server_cert= tls.verify_server_cert if tls else True,
        tls_mode              = tls.mode               if tls else None,
        tls_ca_cert_path      = tls.ca_cert_path       if tls else None,
        tls_client_cert_path  = tls.client_cert_path   if tls else None,
        tls_client_key_path   = tls.client_key_path    if tls else None,
        default_schema        = payload.default_schema or None,
        created_by            = user_id,
    )

    db.add(datasource)
    # flush() sends the INSERT to the DB within the open transaction.
    # The session in get_db() calls commit() after the route handler returns.
    await db.flush()

    return _mask_sensitive_fields(datasource)


async def list_datasources(tenant_id: str, db: AsyncSession) -> list[dict]:
    """
    Returns all datasources registered under a given tenant.
    Credentials and cert paths are ALWAYS stripped from the response.

    After an app restart, this function returns the full list of previously
    registered datasources — connections do NOT disappear on restart because
    they are persisted in the metadata DB.

    Args:
        tenant_id: From the authenticated session
        db:        Injected AsyncSession

    Returns:
        List of safe datasource dicts (most recent first)
    """
    result = await db.execute(
        select(Datasource)
        .where(Datasource.tenant_id == tenant_id)
        .order_by(Datasource.created_at.desc())
    )
    datasources = result.scalars().all()
    return [_mask_sensitive_fields(ds) for ds in datasources]


async def retest_saved_datasource(
    datasource_id: str,
    tenant_id:     str,
    db:            AsyncSession,
) -> dict:
    """
    Re-tests a SAVED datasource using its stored (encrypted) credentials.

    This is the "post-save re-test": the user doesn't need to re-enter credentials.
    The stored AES-256-GCM blob is decrypted at call time, used for the test, and
    never written anywhere or returned in any response.

    This function also works after an app restart — the encrypted credentials
    survive in the metadata DB and are decrypted on demand.

    Args:
        datasource_id: UUID of the saved datasource
        tenant_id:     From the authenticated session (enforces tenant isolation)
        db:            Injected AsyncSession

    Returns:
        Connection test result dict (same shape as test_datasource_connection)

    Raises:
        ValueError: If the datasource is not found or doesn't belong to this tenant
    """
    ds = await _get_datasource(datasource_id, tenant_id, db)
    # check_active=False: re-test is the intended path to reactivate a deactivated
    # datasource, so it must bypass the active-check that guards all other callers.
    config = _datasource_runtime_config(ds, check_active=False)

    try:
        test_result = await test_connection(config)
    except Exception as exc:
        test_result = {
            "success":    False,
            "latency_ms": 0,
            "category":   "UNKNOWN",
            "message":    str(exc),
        }

    # Persist audit fields using a Core SQL UPDATE — avoids ORM attribute expiry
    # and server-default refresh issues (onupdate=func.now() on updated_at) that
    # cause MissingGreenlet errors when doing ORM flush after a long async operation.
    values: dict = {
        "last_tested_at":   datetime.now(timezone.utc).replace(tzinfo=None),
        "last_test_status": "success" if test_result["success"] else "failed",
    }
    if test_result["success"]:
        values["is_active"] = True

    await db.execute(
        sql_update(Datasource)
        .where(
            Datasource.id        == uuid.UUID(datasource_id),
            Datasource.tenant_id == tenant_id,
        )
        .values(**values)
    )

    return test_result


async def get_datasource_schema(
    datasource_id: str,
    tenant_id:     str,
    db:            AsyncSession,
) -> dict:
    """
    Discovers the schema objects accessible to a saved datasource's credentials.
    Implements US 107151: Permission-Scoped Object Browser.

    Decrypts stored credentials, connects to the target DB, runs introspection
    queries, then disconnects. No user data is read — only schema metadata.

    Args:
        datasource_id: UUID of the saved datasource
        tenant_id:     From the authenticated session
        db:            Injected AsyncSession

    Returns:
        Schema discovery result with namespaces, tables, views, and summary counts

    Raises:
        ValueError: If the datasource is not found or doesn't belong to this tenant
    """
    ds = await _get_datasource(datasource_id, tenant_id, db)
    config = _datasource_runtime_config(ds)

    schema_data = await discover_schema(config)

    return {
        "datasource_id":   datasource_id,
        "datasource_name": ds.name,
        "engine":          ds.engine,
        **schema_data,
    }


async def browse_datasource_tables(
    datasource_id: str,
    schema_name:   str,
    offset:        int,
    limit:         int,
    tenant_id:     str,
    db:            AsyncSession,
) -> dict:
    """
    Returns a paginated list of tables/views for a single schema within a saved datasource.

    Args:
        datasource_id: UUID of the saved datasource
        schema_name:   The schema to browse (must not be empty)
        offset:        Objects to skip (pagination cursor)
        limit:         Max objects to return per page
        tenant_id:     From the authenticated session
        db:            Injected AsyncSession

    Returns:
        {datasource_id, datasource_name, engine, schema_name, objects,
         total_tables, total_views, offset, limit, has_more}

    Raises:
        ValueError: If the datasource is not found or schema_name is empty
    """
    if not schema_name or not schema_name.strip():
        raise ValueError("schema_name must not be empty")

    ds = await _get_datasource(datasource_id, tenant_id, db)
    config = _datasource_runtime_config(ds)

    browse_data = await _browse_tables(config, schema_name.strip(), offset, limit)

    return {
        "datasource_id":   datasource_id,
        "datasource_name": ds.name,
        "engine":          ds.engine,
        "schema_name":     schema_name.strip(),
        **browse_data,
    }


async def get_table_columns(
    datasource_id: str,
    schema_name:   str,
    table_name:    str,
    tenant_id:     str,
    db:            AsyncSession,
) -> dict:
    if not schema_name or not schema_name.strip():
        raise ValueError("schema_name must not be empty")
    if not table_name or not table_name.strip():
        raise ValueError("table_name must not be empty")

    ds = await _get_datasource(datasource_id, tenant_id, db)
    config = _datasource_runtime_config(ds)

    columns = await _inspect_columns(config, schema_name.strip(), table_name.strip())

    return {
        "datasource_id":   datasource_id,
        "datasource_name": ds.name,
        "engine":          ds.engine,
        "schema_name":     schema_name.strip(),
        "table_name":      table_name.strip(),
        "columns":         columns,
    }


async def search_datasource_tables(
    datasource_id: str,
    schema_name:   str,
    search_query:  str,
    tenant_id:     str,
    db:            AsyncSession,
) -> dict:
    """
    Searches for tables/views by name within a saved datasource's schema.

    Args:
        datasource_id: UUID of the saved datasource
        schema_name:   The schema to search in
        search_query:  Partial table name to search for (case-insensitive)
        tenant_id:     From the authenticated session
        db:            Injected AsyncSession

    Returns:
        {datasource_id, datasource_name, engine, schema_name, objects, total}

    Raises:
        ValueError: If the datasource is not found, schema_name is empty, or search_query is empty
    """
    if not schema_name or not schema_name.strip():
        raise ValueError("schema_name must not be empty")
    if not search_query or not search_query.strip():
        raise ValueError("search_query must not be empty")

    ds = await _get_datasource(datasource_id, tenant_id, db)
    config = _datasource_runtime_config(ds)

    search_data = await _search_tables(config, schema_name.strip(), search_query.strip())

    return {
        "datasource_id":   datasource_id,
        "datasource_name": ds.name,
        "engine":          ds.engine,
        "schema_name":     schema_name.strip(),
        **search_data,
    }


async def deactivate_datasource(
    datasource_id: str,
    tenant_id:     str,
    db:            AsyncSession,
) -> dict:
    """
    Deactivates a saved datasource without deleting it.
    Sets is_active=False, preventing schema/table browsing until re-tested successfully.

    Raises:
        ValueError: If the datasource is not found or doesn't belong to this tenant
    """
    ds = await _get_datasource(datasource_id, tenant_id, db)
    ds.is_active = False
    await db.flush()
    await db.refresh(ds)
    return _mask_sensitive_fields(ds)


async def get_datasource_runtime_config(
    datasource_id: str,
    tenant_id:     str,
    db:            AsyncSession,
) -> dict:
    """
    Returns the runtime connection config (with decrypted credentials) for a datasource.
    Used by other modules that need to open a connection without needing the ORM object.

    Raises:
        ValueError: If the datasource is not found or doesn't belong to this tenant
    """
    ds = await _get_datasource(datasource_id, tenant_id, db)
    return _datasource_runtime_config(ds)


async def delete_datasource(
    datasource_id: str,
    tenant_id:     str,
    db:            AsyncSession,
) -> None:
    """
    Permanently deletes a saved datasource and its encrypted credentials.
    Also cascades deletion to all annotation data (table/column annotations and
    relationships) since those tables have no FK constraint to datasources.

    Args:
        datasource_id: UUID of the datasource to delete
        tenant_id:     From the authenticated session (enforces tenant isolation)
        db:            Injected AsyncSession

    Raises:
        ValueError: If the datasource is not found or doesn't belong to this tenant
    """
    from app.modules.annotations.service import delete_datasource_annotations

    ds = await _get_datasource(datasource_id, tenant_id, db)

    # Remove all M2 annotation data for this datasource before deleting the record.
    await delete_datasource_annotations(datasource_id, db)

    await db.delete(ds)
    await db.flush()


# =============================================================================
# Internal helpers
# =============================================================================

async def _get_datasource(datasource_id: str, tenant_id: str, db: AsyncSession) -> Datasource:
    result = await db.execute(
        select(Datasource).where(
            Datasource.id        == uuid.UUID(datasource_id),
            Datasource.tenant_id == tenant_id,
        )
    )
    ds = result.scalar_one_or_none()
    if ds is None:
        raise ValueError(f"Data source '{datasource_id}' not found.")
    return ds


def _datasource_runtime_config(datasource: Datasource, *, check_active: bool = True) -> dict:
    """
    Builds the runtime connection config dict for a datasource.

    check_active=True (default): raises DatasourceInactiveError if the datasource is
    deactivated — this is the single enforcement point for all runtime DB operations.
    Pass check_active=False ONLY from retest_saved_datasource, which is the intended
    path to reactivate a deactivated connection.
    """
    if check_active and not datasource.is_active:
        raise DatasourceInactiveError(
            "Datasource is inactive. Reactivate or re-test the datasource before using it."
        )
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


def _mask_sensitive_fields(datasource: Datasource) -> dict:
    """
    Converts an ORM Datasource instance to a safe API-response dict.

    Fields stripped (NEVER returned to any client):
      encrypted_credentials  — AES-256-GCM blob
      tls_*_cert_path        — Server-side filesystem paths

    Fields added (presence indicators only):
      has_credentials  — True if credentials are stored (without revealing them)
      has_ca_cert      — True if a CA cert file is configured
      has_client_cert  — True if a client cert file is configured

    Args:
        datasource: Raw ORM model instance from a DB query

    Returns:
        Safe dict ready for the DatasourceResponse Pydantic model
    """
    return {
        "id":                     str(datasource.id),
        "name":                   datasource.name,
        "tenant_id":              datasource.tenant_id,
        "engine":                 datasource.engine,
        "host":                   datasource.host,
        "port":                   datasource.port,
        "database_name":          datasource.database_name,
        "oracle_connection_type": datasource.oracle_connection_type,
        "auth_method":            datasource.auth_method,
        "tls_enabled":            datasource.tls_enabled,
        "tls_mode":               datasource.tls_mode,
        "created_at":             datasource.created_at.isoformat() if datasource.created_at else None,
        "updated_at":             datasource.updated_at.isoformat() if datasource.updated_at else None,
        "created_by":             datasource.created_by,
        "last_tested_at":         datasource.last_tested_at.isoformat() if datasource.last_tested_at else None,
        "last_test_status":       datasource.last_test_status,
        "default_schema":         datasource.default_schema,
        "is_active":              datasource.is_active,
        # Presence flags only — never the actual values
        "has_credentials":        bool(datasource.encrypted_credentials),
        "has_ca_cert":            bool(datasource.tls_ca_cert_path),
        "has_client_cert":        bool(datasource.tls_client_cert_path),
    }
