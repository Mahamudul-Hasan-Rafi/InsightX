# api/app/modules/datasources/router.py
#
# PURPOSE:
#   HTTP layer — FastAPI APIRouter with 6 endpoints.
#   Parses requests, delegates to service.py, and shapes responses.
#   Does NOT contain business logic — only HTTP concerns (status codes,
#   request parsing, response serialization, dependency injection).
#
# ENDPOINTS:
#   POST  /test          Pre-save connection test (plaintext credentials from form)
#   POST  /upload        Upload TLS cert, Oracle Wallet, or Kerberos keytab
#   POST  /              Create and save a datasource with encrypted credentials
#   GET   /              List all datasources for the current tenant
#   POST  /{id}/test     Re-test a SAVED datasource (uses decrypted stored credentials)
#   GET   /{id}/schema   Discover schema objects for a saved datasource (US 107151)
#
# AUTHENTICATION:
#   CurrentUser dependency from app.core.security resolves the caller from the
#   access_token HttpOnly cookie (BFF flow) or an Authorization: Bearer header.
#
# NOTE ON /test RESPONSE CODE:
#   Both test endpoints always return HTTP 200 — even when the DB connection fails.
#   A failed DB connection is a valid, expected result, not an HTTP error.
#   Using HTTP 5xx here would confuse error-handling middleware.

import time
import secrets
from pathlib import Path
from typing import Annotated, TypeAlias

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.guards import require_role
from app.db.session import get_db
from app.modules.datasources import service
from app.modules.datasources.service import DatasourceInactiveError
from app.modules.datasources.schemas import (
    ColumnMetaListResponse,
    DatasourceListResponse,
    DatasourcePayload,
    DatasourceResponse,
    FileUploadResponse,
    SchemaDiscoveryResponse,
    SearchTableResponse,
    TableBrowseResponse,
    TestConnectionResponse,
)

router = APIRouter()

# ---------------------------------------------------------------------------
# Allowed file types for secure uploads
# ---------------------------------------------------------------------------

_ALLOWED_EXTENSIONS = {
    ".pem", ".crt", ".cer", ".key",   # TLS certificates and private keys
    ".p12", ".sso",                   # Oracle Wallet formats
    ".keytab", ".kt",                 # Kerberos keytab files
}

_ALLOWED_UPLOAD_TYPES = {
    "ca_cert", "client_cert", "client_key", "wallet", "keytab"
}

# Create the upload directory at import time.
# In production, this directory must be outside the webroot.
_upload_dir = Path(settings.secure_files_dir)
_upload_dir.mkdir(parents=True, exist_ok=True)


DB: TypeAlias = Annotated[AsyncSession, Depends(get_db)]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/test",
    response_model=TestConnectionResponse,
    summary="Test a connection WITHOUT saving it",
    description=(
        "Tests connection parameters by opening a live connection and running SELECT 1. "
        "Non-destructive — no data is read or written. "
        "Always returns HTTP 200; success/failure is in the response body."
    ),
)
async def test_connection(
    payload:      DatasourcePayload,
    current_user: dict = Depends(require_role("feat:datasource:view")),
) -> TestConnectionResponse:
    """
    Pre-save connection test. Called by the wizard's 'Test Connection' button.
    Credentials come from the form in plaintext — they are NOT stored here.
    """
    # No DB session needed — this test is entirely non-destructive and stateless
    result = await service.test_datasource_connection(payload)
    return TestConnectionResponse(**result)


@router.post(
    "/upload",
    response_model=FileUploadResponse,
    summary="Upload a TLS cert, Oracle Wallet, or Kerberos keytab",
    description=(
        "Stores the uploaded file on the server filesystem outside the webroot. "
        "Returns the server-side file path to embed in the datasource payload. "
        "Accepted types: ca_cert | client_cert | client_key | wallet | keytab."
    ),
)
async def upload_secure_file(
    current_user: dict = Depends(require_role("feat:datasource:create")),
    file: UploadFile = File(..., description="The file to upload"),
    type: str        = Form(..., description="ca_cert | client_cert | client_key | wallet | keytab"),
) -> FileUploadResponse:
    """
    Handles secure file uploads for TLS certs, Oracle Wallets, and Kerberos keytabs.
    Files are stored outside the webroot — they cannot be downloaded via HTTP.
    """
    # --- Validate the upload type ---
    if type not in _ALLOWED_UPLOAD_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid upload type '{type}'. Must be one of: {sorted(_ALLOWED_UPLOAD_TYPES)}",
        )

    # --- Validate the file extension ---
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    ext = Path(file.filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{ext}' is not permitted. Allowed: {sorted(_ALLOWED_EXTENSIONS)}",
        )

    # --- Validate the file size BEFORE writing to disk ---
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    contents  = await file.read()
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.max_upload_size_mb} MB limit",
        )

    # --- Generate a non-predictable filename ---
    # Format: tenantId-timestamp-randomHex.ext
    # This prevents filename collisions and makes filenames non-guessable.
    tenant_id     = current_user["tenant_id"]
    ts            = int(time.time() * 1000)
    random_hex    = secrets.token_hex(4)
    safe_filename = f"{tenant_id}-{ts}-{random_hex}{ext}"
    dest_path     = _upload_dir / safe_filename

    # --- Write the file asynchronously (aiofiles avoids blocking the event loop) ---
    async with aiofiles.open(dest_path, "wb") as out:
        await out.write(contents)

    return FileUploadResponse(
        path     = str(dest_path),
        filename = safe_filename,
        type     = type,
    )


@router.post(
    "",
    response_model=DatasourceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create and save a new datasource",
    description=(
        "Creates a new datasource record with AES-256-GCM encrypted credentials. "
        "Call this after a successful /test response. "
        "Credentials are never stored in plaintext."
    ),
)
@router.post("/", response_model=DatasourceResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_datasource(
    payload:      DatasourcePayload,
    db:           DB,
    current_user: dict = Depends(require_role("api:datasource:create")),
) -> DatasourceResponse:
    """
    Saves a new datasource. The wizard should only call this after a successful /test.
    (We don't enforce this server-side — it's a UI concern, not a data integrity issue.)
    """
    try:
        result = await service.create_datasource(
            payload   = payload,
            tenant_id = current_user["tenant_id"],
            user_id   = current_user["id"],
            db        = db,
        )
        return DatasourceResponse(**result)

    except ValueError as exc:
        # service raises ValueError for duplicate names
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )


@router.get(
    "",
    response_model=DatasourceListResponse,
    summary="List all datasources for the current tenant",
    description=(
        "Returns all registered datasources for the authenticated tenant. "
        "Credentials and cert paths are NEVER included in the response. "
        "Datasources persist across app restarts."
    ),
)
@router.get("/", response_model=DatasourceListResponse, include_in_schema=False)
async def list_datasources(
    db:           DB,
    current_user: dict = Depends(require_role("feat:datasource:view")),
) -> DatasourceListResponse:
    """
    Lists all saved datasources for the tenant. Safe to call after app restart —
    all registered connections are persisted in the metadata DB.
    """
    print(f"list_datasources called for tenant_id={current_user['tenant_id']}")
    sources = await service.list_datasources(
        tenant_id = current_user["tenant_id"],
        db        = db,
    )
    return DatasourceListResponse(
        data  = [DatasourceResponse(**s) for s in sources],
        count = len(sources),
    )


@router.post(
    "/{datasource_id}/test",
    response_model=TestConnectionResponse,
    summary="Re-test a saved datasource using stored credentials",
    description=(
        "Tests an already-saved datasource WITHOUT requiring the user to re-enter credentials. "
        "Decrypts stored AES-256-GCM credentials at runtime, runs SELECT 1, then discards the plaintext. "
        "Works after app restarts — stored credentials survive in the metadata DB. "
        "Always returns HTTP 200; success/failure is in the response body."
    ),
)
async def retest_saved_datasource(
    datasource_id: str,
    db:            DB,
    current_user:  dict = Depends(require_role("feat:datasource:view")),
) -> TestConnectionResponse:
    """
    Post-save re-test. Called from the datasource list page to check connectivity.
    The user does NOT need to re-enter credentials — they are stored encrypted.
    Updates last_tested_at and last_test_status on the saved record.
    """
    try:
        result = await service.retest_saved_datasource(
            datasource_id = datasource_id,
            tenant_id     = current_user["tenant_id"],
            db            = db,
        )
        return TestConnectionResponse(**result)

    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    except Exception as exc:
        # Decryption failure (wrong key, tampered data) should surface clearly
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to decrypt credentials for datasource '{datasource_id}': {exc}",
        )


@router.get(
    "/{datasource_id}/schema",
    response_model=SchemaDiscoveryResponse,
    summary="Discover schema objects for a saved datasource (US 107151)",
    description=(
        "Returns the schemas, tables, and views accessible to the datasource's credentials. "
        "Uses the same stored credentials as re-test — no re-entry required. "
        "Introspects only metadata tables (information_schema, ALL_TABLES, etc.) — no user data is read."
    ),
)
async def get_datasource_schema(
    datasource_id: str,
    db:            DB,
    current_user:  dict = Depends(require_role("api:datasource:read")),
) -> SchemaDiscoveryResponse:
    """
    Permission-scoped object browser for a saved datasource.
    Returns only the objects the datasource's authenticated user can see.
    """
    try:
        result = await service.get_datasource_schema(
            datasource_id = datasource_id,
            tenant_id     = current_user["tenant_id"],
            db            = db,
        )
        return SchemaDiscoveryResponse(**result)

    except DatasourceInactiveError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Schema discovery failed: {exc}",
        )


@router.get(
    "/{datasource_id}/tables",
    response_model=TableBrowseResponse,
    summary="Browse tables/views for a single schema with pagination",
    description=(
        "Returns a paginated list of tables and views for one schema in a saved datasource. "
        "Use offset/limit for 'Load more' pagination. "
        "total_tables and total_views reflect the full schema counts, not just this page."
    ),
)
async def browse_schema_tables(
    datasource_id: str,
    db:            DB,
    current_user:  dict = Depends(require_role("api:datasource:read")),
    schema_name:   str           = Query(..., min_length=1, description="Schema/owner name to browse"),
    offset:        int           = Query(0,   ge=0,         description="Objects to skip"),
    limit:         int           = Query(10,  ge=1, le=200, description="Max objects per page"),
) -> TableBrowseResponse:
    """
    Paginated table browser for a single schema.
    Called when the object browser opens and on each 'Load more' click.
    """
    try:
        result = await service.browse_datasource_tables(
            datasource_id = datasource_id,
            schema_name   = schema_name,
            offset        = offset,
            limit         = limit,
            tenant_id     = current_user["tenant_id"],
            db            = db,
        )
        return TableBrowseResponse(**result)

    except DatasourceInactiveError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Table browsing failed: {exc}",
        )


@router.get(
    "/{datasource_id}/search",
    response_model=SearchTableResponse,
    summary="Search for tables by name in a schema",
    description=(
        "Searches for tables and views by name using case-insensitive partial matching. "
        "No pagination — returns all matches."
    ),
)
async def search_datasource_tables(
    datasource_id: str,
    db:            DB,
    current_user:  dict = Depends(require_role("api:datasource:read")),
    schema_name:   str = Query(..., min_length=1, description="Schema/owner name to search in"),
    query:         str = Query(..., min_length=1, description="Table name search term (partial, case-insensitive)"),
) -> SearchTableResponse:
    """
    Search for tables by name within a schema.
    Returns all matching tables and views.
    """
    try:
        result = await service.search_datasource_tables(
            datasource_id = datasource_id,
            schema_name   = schema_name,
            search_query  = query,
            tenant_id     = current_user["tenant_id"],
            db            = db,
        )
        return SearchTableResponse(**result)

    except DatasourceInactiveError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Table search failed: {exc}",
        )


@router.get(
    "/{datasource_id}/columns",
    response_model=ColumnMetaListResponse,
    summary="Get column metadata for a table",
    description=(
        "Returns column names, formatted types, nullability, and PK/FK flags "
        "for a single table or view within a saved datasource."
    ),
)
async def get_table_columns(
    datasource_id: str, 
    db:        DB,
    current_user: dict = Depends(require_role("api:datasource:read")),
    schema_name:   str = Query(..., min_length=1, description="Schema/owner name"),
    table_name:    str = Query(..., min_length=1, description="Table or view name"),
) -> ColumnMetaListResponse:
    try:
        result = await service.get_table_columns(
            datasource_id = datasource_id,
            schema_name   = schema_name,
            table_name    = table_name,
            tenant_id     = current_user["tenant_id"],
            db            = db,
        )
        return ColumnMetaListResponse(**result)

    except DatasourceInactiveError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Column metadata fetch failed: {exc}",
        )


@router.post(
    "/{datasource_id}/sync-relationships",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger background FK relationship discovery",
    description=(
        "Resolves datasource credentials then dispatches a background task that "
        "introspects FK constraints in the target schema and syncs them to the "
        "table_relationships table (is_discovered=True rows only). "
        "Returns 202 immediately — discovery runs after the response is sent."
    ),
)
async def sync_relationships(
    datasource_id:    str,
    background_tasks: BackgroundTasks,
     db:               DB,
    current_user:     dict = Depends(require_role("api:datasource:read")),
    schema_name:      str = Query(..., min_length=1, description="Schema to discover FK relationships in"),
) -> dict:
    try:
        config = await service.get_datasource_runtime_config(
            datasource_id = datasource_id,
            tenant_id     = current_user["tenant_id"],
            db            = db,
        )
    except DatasourceInactiveError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    from app.modules.annotations.service import run_sync_in_background
    background_tasks.add_task(
        run_sync_in_background,
        datasource_id = datasource_id,
        schema_name   = schema_name,
        tenant_id     = current_user["tenant_id"],
        config        = config,
    )
    return {"status": "accepted", "datasource_id": datasource_id, "schema_name": schema_name}


@router.patch(
    "/{datasource_id}/deactivate",
    response_model=DatasourceResponse,
    summary="Deactivate a saved datasource",
    description=(
        "Sets is_active=False on a datasource without deleting it. "
        "Schema browsing is blocked until the connection is re-tested successfully. "
        "Re-test (POST /{id}/test) re-activates the connection if it succeeds."
    ),
)
async def deactivate_datasource(
    datasource_id: str,
    db:            DB,
    current_user:  dict = Depends(require_role("feat:datasource:create")),
) -> DatasourceResponse:
    """Deactivates a saved datasource. Re-test to reactivate."""
    try:
        result = await service.deactivate_datasource(
            datasource_id = datasource_id,
            tenant_id     = current_user["tenant_id"],
            db            = db,
        )
        return DatasourceResponse(**result)

    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.delete(
    "/{datasource_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a saved datasource",
    description=(
        "Permanently removes a datasource record and its encrypted credentials. "
        "This action is irreversible — the connection will need to be re-added manually."
    ),
)
async def delete_datasource(
    datasource_id: str,
    db:            DB,
    current_user:  dict = Depends(require_role("api:datasource:delete")),
) -> None:
    """
    Deletes a saved datasource. Returns 204 No Content on success.
    """
    try:
        await service.delete_datasource(
            datasource_id = datasource_id,
            tenant_id     = current_user["tenant_id"],
            db            = db,
        )

    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
