# api/app/modules/datasources/drivers/postgres_driver.py
#
# PURPOSE:
#   Adapter that wraps asyncpg to test a PostgreSQL connection.
#   Converts engine-specific exceptions into the normalised result dict
#   that connection_tester.py expects: {success, latency_ms, raw_error?}
#
# LIBRARY: asyncpg (pip install asyncpg)
#   asyncpg is fully async — no thread wrapper needed.
#
# TLS NOTES:
#   asyncpg's `ssl` parameter accepts:
#     None / False   → no TLS
#     True           → require TLS, verify server cert using system CA store
#     ssl.SSLContext → custom context for custom CA, client certs, or skip verify
#
#   We build a custom SSLContext when TLS is enabled to support:
#     - Custom CA certificates (private PKI)
#     - Client certificates (mutual TLS / mTLS)
#     - Disabling server cert verification (for self-signed certs in dev)

import ssl
import time
from typing import Optional
import asyncpg


def _build_ssl_context(tls: dict) -> Optional[ssl.SSLContext]:
    """
    Builds an ssl.SSLContext from the TLS config dict.
    Returns None if TLS is disabled.

    Args:
        tls: TLS config dict with keys: enabled, verify_server_cert,
             ca_cert_path, client_cert_path, client_key_path

    Returns:
        ssl.SSLContext or None
    """
    if not tls or not tls.get("enabled"):
        return None

    if tls.get("verify_server_cert", True):
        # VERIFY the server certificate (secure by default)
        if tls.get("ca_cert_path"):
            # Use a custom CA cert file (cafile= reads the PEM from disk)
            ctx = ssl.create_default_context(cafile=tls["ca_cert_path"])
        else:
            # Use the system's built-in CA store (trusts publicly trusted CAs)
            ctx = ssl.create_default_context()
    else:
        # User explicitly opted to SKIP server cert verification.
        # This is a security risk — shown as a warning in the UI.
        # Only acceptable for development with self-signed certs.
        ctx              = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE

    # Mutual TLS (mTLS) — client presents its own certificate to the server
    if tls.get("client_cert_path") and tls.get("client_key_path"):
        ctx.load_cert_chain(
            certfile=tls["client_cert_path"],
            keyfile=tls["client_key_path"],
        )

    return ctx


async def test_postgres_connection(config: dict) -> dict:
    """
    Tests a PostgreSQL connection by establishing it and running SELECT 1.
    Non-destructive — no data is read or written.

    Args:
        config: Connection config dict.
                Keys: host, port, database, credentials {username, password},
                auth_method, tls (optional)

    Returns:
        {"success": bool, "latency_ms": int}
        On failure: adds "raw_error": Exception for classification in connection_tester.py
    """
    host        = config["host"]
    port        = int(config["port"])
    database    = config["database"]
    credentials = config["credentials"]
    tls         = config.get("tls") or {}

    ssl_context = _build_ssl_context(tls)

    start_ms = int(time.time() * 1000)
    conn     = None

    try:
        conn = await asyncpg.connect(
            host     = host,
            port     = port,
            database = database,
            user     = credentials["username"],
            password = credentials["password"],
            ssl      = ssl_context,
            timeout  = 10.0,   # asyncpg's own connect timeout in seconds
        )

        # SELECT 1 is the cheapest possible query to confirm the connection is live.
        # asyncpg.connect() alone doesn't guarantee a fully authenticated session
        # on all server versions without at least one actual query.
        await conn.fetchval("SELECT 1")

        return {
            "success":    True,
            "latency_ms": int(time.time() * 1000) - start_ms,
        }

    except Exception as exc:
        return {
            "success":    False,
            "latency_ms": int(time.time() * 1000) - start_ms,
            "raw_error":  exc,   # Passed to connection_tester._classify_error()
        }

    finally:
        # Always close the connection — asyncpg connections are not pooled here
        if conn:
            try:
                await conn.close()
            except Exception:
                pass   # Ignore cleanup errors — the test result is already determined
