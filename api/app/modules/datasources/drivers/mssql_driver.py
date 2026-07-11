# api/app/modules/datasources/drivers/mssql_driver.py
#
# PURPOSE:
#   Adapter that wraps pyodbc to test an MS SQL Server connection.
#   pyodbc is synchronous — runs in asyncio.to_thread() to avoid blocking
#   FastAPI's async event loop.
#
# LIBRARY: pyodbc (pip install pyodbc)
#
# SYSTEM REQUIREMENT:
#   Microsoft ODBC Driver for SQL Server must be installed on the backend host OS.
#   Download: https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server
#   Ubuntu/Debian: sudo apt-get install msodbcsql18
#   macOS:         brew install msodbcsql18
#
# AUTH METHODS SUPPORTED:
#   password  — SQL Server login (username + password in connection string)
#   windows   — NTLM / Windows Integrated Auth (Trusted_Connection=yes)
#               Only works when the backend runs on a Windows host in the same AD domain,
#               OR on Linux with proper Kerberos/krb5 configuration.
#   azure_ad  — Access token via SQL_COPT_SS_ACCESS_TOKEN attribute.
#               The token must be pre-acquired via the Azure AD OAuth2 MSAL flow.
#               Token encoding: UTF-16-LE wrapped in a struct (Microsoft-specified format).
#
# TLS:
#   Controlled via Encrypt= and TrustServerCertificate= in the connection string.
#   Custom CA certs with MSSQL ODBC must be installed in the OS certificate store —
#   there is no file-based cafile= option like asyncpg/ssl.SSLContext.

import asyncio
import struct
import time
import pyodbc


def _get_odbc_driver() -> str:
    """
    Returns the name of the first available Microsoft ODBC Driver for SQL Server.
    Checks in order of preference (newest first).

    Raises:
        RuntimeError: If no compatible driver is found.
    """
    available = pyodbc.drivers()
    for preferred in [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 13 for SQL Server",
    ]:
        if preferred in available:
            return preferred

    raise RuntimeError(
        "No Microsoft ODBC Driver for SQL Server found on this host. "
        "Install from: https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server"
    )


def _sync_test_mssql(config: dict) -> dict:
    """
    Synchronous MSSQL connection test.
    Called via asyncio.to_thread() — must NOT use async/await.
    Defined as a top-level function (not a lambda) because to_thread needs a callable.
    """
    start_ms    = int(time.time() * 1000)
    host        = config["host"]
    port        = int(config["port"])
    database    = config["database"]
    auth_method = config["auth_method"]
    credentials = config["credentials"]
    tls         = config.get("tls") or {}

    # Detect installed ODBC driver
    try:
        driver = _get_odbc_driver()
    except RuntimeError as exc:
        return {"success": False, "latency_ms": 0, "raw_error": exc}

    # Build connection string parts
    conn_parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={host},{port}",
        f"DATABASE={database}",
        "Connection Timeout=10",   # In seconds
    ]

    # TLS / Encryption
    if tls.get("enabled"):
        conn_parts.append("Encrypt=yes")
        # TrustServerCertificate=yes means DON'T verify server cert (inverse of our flag)
        trust = "no" if tls.get("verify_server_cert", True) else "yes"
        conn_parts.append(f"TrustServerCertificate={trust}")
    else:
        conn_parts.append("Encrypt=no")

    # Auth-method-specific additions
    if auth_method == "password":
        conn_parts.append(f"UID={credentials['username']}")
        conn_parts.append(f"PWD={credentials['password']}")

    elif auth_method == "windows":
        # NTLM Windows Integrated Auth — uses the process's domain credentials
        conn_parts.append("Trusted_Connection=yes")
        # Optional explicit domain\username override
        if credentials.get("domain") and credentials.get("username"):
            conn_parts.append(f"UID={credentials['domain']}\\{credentials['username']}")
            if credentials.get("password"):
                conn_parts.append(f"PWD={credentials['password']}")

    elif auth_method == "azure_ad":
        # Azure AD token auth requires Encrypt=yes (even if not explicitly enabled)
        if not tls.get("enabled"):
            conn_parts.append("Encrypt=yes")
            conn_parts.append("TrustServerCertificate=no")
        # The actual token is passed via attrs_before (below), not in the conn string

    conn_str = ";".join(conn_parts)
    conn     = None

    try:
        if auth_method == "azure_ad":
            # Microsoft-specified encoding for Azure AD access tokens:
            # Encode as UTF-16-LE, then pack as a struct with a little-endian uint32 length prefix.
            # Reference: https://docs.microsoft.com/en-us/sql/connect/odbc/using-azure-active-directory
            SQL_COPT_SS_ACCESS_TOKEN = 1256
            token        = credentials["access_token"].encode("utf-16-le")
            token_struct = struct.pack(f"<I{len(token)}s", len(token), token)
            conn = pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})
        else:
            conn = pyodbc.connect(conn_str)

        # Run a trivial query to confirm full authentication succeeded
        cursor = conn.cursor()
        cursor.execute("SELECT 1 AS connected")
        cursor.fetchone()
        cursor.close()

        return {
            "success":    True,
            "latency_ms": int(time.time() * 1000) - start_ms,
        }

    except Exception as exc:
        return {
            "success":    False,
            "latency_ms": int(time.time() * 1000) - start_ms,
            "raw_error":  exc,
        }

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


async def test_mssql_connection(config: dict) -> dict:
    """
    Async wrapper for the synchronous pyodbc test.

    asyncio.to_thread() runs the synchronous function in a ThreadPoolExecutor,
    preventing it from blocking FastAPI's async event loop.
    Requires Python 3.9+ (Python 3.11+ recommended).

    Args:
        config: Normalised connection config dict

    Returns:
        {"success": bool, "latency_ms": int, "raw_error"?: Exception}
    """
    return await asyncio.to_thread(_sync_test_mssql, config)
