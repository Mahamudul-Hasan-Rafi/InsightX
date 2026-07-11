# api/app/modules/datasources/connection_tester.py
#
# PURPOSE:
#   Single entry point for all connection tests.
#   Responsibilities:
#     1. Dispatch to the correct driver based on config["engine"] (Strategy pattern)
#     2. Enforce a 10-second hard outer timeout via asyncio.wait_for
#     3. Classify raw driver exceptions into user-friendly category strings
#
# WHY CLASSIFY HERE (not in the drivers)?
#   Different drivers raise different exception types for the same root cause.
#   For example, "wrong password" looks like:
#     asyncpg:         asyncpg.InvalidPasswordError (message: "password authentication failed")
#     pyodbc/MSSQL:    pyodbc.Error (message: "[28000] Login failed for user 'X'")
#     python-oracledb: oracledb.DatabaseError (message: "ORA-01017: invalid username/password")
#   Centralising classification here means the frontend always receives the same
#   small set of category strings regardless of which engine is being tested.
#
# ERROR CATEGORIES (returned in the response body, used by the frontend):
#   AUTH_FAILED           Wrong username, password, wallet, or Azure AD token
#   HOST_UNREACHABLE      Cannot reach the host (firewall, DNS failure, port closed)
#   TLS_HANDSHAKE_FAILED  SSL/TLS negotiation failed (wrong cert, wrong mode)
#   TIMEOUT               No response within 10 seconds
#   UNSUPPORTED_CONFIG    Feature requires server-side setup (Kerberos, ODBC driver)
#   UNKNOWN               Unclassified error (raw message included for debugging)

import asyncio

from app.core.config import settings
from app.modules.datasources.drivers.postgres_driver import test_postgres_connection
from app.modules.datasources.drivers.mssql_driver    import test_mssql_connection
from app.modules.datasources.drivers.oracle_driver   import test_oracle_connection
from app.modules.datasources.drivers.delta_driver    import test_delta_connection

# Hard outer timeout — safety net if a driver's own timeout doesn't fire in time
_TIMEOUT_SECONDS = 10

# Per-engine overrides. Delta's cold-start SparkSession build (JVM boot +
# first-time Delta package resolution) routinely exceeds the 10s default.
_TIMEOUT_OVERRIDES = {
    "delta": settings.spark_connection_timeout_seconds,
}


def _classify_error(engine: str, error: Exception) -> dict:
    """
    Maps a raw driver exception to a user-friendly error category + message.

    Uses substring matching on the lowercased error message because different
    driver versions and OS configurations produce different exception types for
    the same logical failure. String matching is more portable than isinstance.

    Args:
        engine: 'postgresql' | 'mssql' | 'oracle' (used for future engine-specific rules)
        error:  The exception raised by the driver

    Returns:
        {"category": str, "message": str}
    """
    msg = str(error).lower()

    # --- Authentication failures ---
    if any(p in msg for p in [
        "password authentication failed",     # asyncpg
        "invalid password",                   # asyncpg InvalidPasswordError
        "login failed",                       # pyodbc MSSQL: "Login failed for user"
        "invalid username/password",          # python-oracledb
        "ora-01017",                          # Oracle: invalid username/password
        "ora-01005",                          # Oracle: null password given
        "28000",                              # SQLSTATE: invalid authorization specification
        "authentication failed",
        "access denied",
        "invalid token",                      # Azure AD token errors
    ]):
        return {
            "category": "AUTH_FAILED",
            "message":  "Authentication failed. Check your username, password, or token.",
        }

    # --- Host unreachable / connection refused ---
    if any(p in msg for p in [
        "connection refused",
        "could not connect to server",          # asyncpg
        "no such host",
        "name or service not known",            # DNS failure on Linux
        "tns:no listener",                      # Oracle: nothing listening on port
        "ora-12541",                            # Oracle: no listener
        "server is not found or not accessible",
        "network-related or instance-specific", # MSSQL generic network error
        "could not be resolved",
        "nodename nor servname provided",       # macOS DNS failure
        "getaddrinfo failed",
        "[08001]",                              # SQLSTATE: client unable to connect
        "call from",                            # py4j: "Call From <host> to <master> failed"
        "master is unresponsive",               # Spark: master registration failure
        "all masters are unresponsive",         # Spark: standalone master unreachable
        "connection call to",                   # Hadoop/HDFS: namenode unreachable
    ]):
        return {
            "category": "HOST_UNREACHABLE",
            "message":  "Cannot reach the host. Check the hostname, port, and that the database is running.",
        }

    # --- TLS / SSL failures ---
    if any(p in msg for p in [
        "ssl", "tls", "certificate", "handshake",
        "ora-29024",              # Oracle: certificate validation failure
        "certificate verify failed",
        "ssl handshake failed",
        "ssl routines",
        "[08001] ssl",
    ]):
        return {
            "category": "TLS_HANDSHAKE_FAILED",
            "message":  "TLS/SSL handshake failed. Check your certificates, SSL mode, and whether the server requires TLS.",
        }

    # --- Timeout ---
    if any(p in msg for p in [
        "timeout", "timed out",
        "ora-12170",              # Oracle: connect timeout
        "connection timed out",
    ]):
        return {
            "category": "TIMEOUT",
            "message":  "Connection timed out after 10 seconds. The host may be slow or unreachable.",
        }

    # --- Unsupported configuration ---
    if any(p in msg for p in [
        "not supported in thin mode",      # python-oracledb: Kerberos requires Thick Mode
        "kerberos", "thick mode required",
        "no microsoft odbc driver",        # raised by mssql_driver._get_odbc_driver()
    ]):
        return {
            "category": "UNSUPPORTED_CONFIG",
            "message": (
                "This configuration requires additional server-side setup. "
                "Kerberos requires Oracle Thick Mode (init_oracle_client). "
                "MSSQL connections require the Microsoft ODBC Driver to be installed on the server."
            ),
        }

    # --- Fallback ---
    return {
        "category": "UNKNOWN",
        "message":  f"Connection failed: {str(error)}",
    }


async def test_connection(config: dict) -> dict:
    """
    Tests a database connection using the appropriate driver.
    This is the ONLY function the service layer calls — it never imports drivers directly.

    The test is entirely non-destructive:
      - Only SELECT 1 (or SELECT 1 FROM DUAL for Oracle) is executed.
      - No schema introspection, no data read, no data written.
      - No connection is persisted — it is opened and immediately closed.

    Args:
        config: Full datasource config dict.
                Keys: engine, host, port, database, auth_method, credentials, tls (optional)
                Credentials MUST be in plaintext (not encrypted).

    Returns:
        On success: {"success": True,  "latency_ms": int}
        On failure: {"success": False, "latency_ms": int, "category": str, "message": str}
    """
    # Strategy pattern: select driver at runtime based on engine string
    driver_map = {
        "postgresql": test_postgres_connection,
        "mssql":      test_mssql_connection,
        "oracle":     test_oracle_connection,
        "delta":      test_delta_connection,
    }

    driver_fn = driver_map.get(config.get("engine"))

    if driver_fn is None:
        return {
            "success":    False,
            "latency_ms": 0,
            "category":   "UNSUPPORTED_ENGINE",
            "message":    f"Engine '{config.get('engine')}' is not supported.",
        }

    timeout_seconds = _TIMEOUT_OVERRIDES.get(config.get("engine"), _TIMEOUT_SECONDS)

    try:
        # asyncio.wait_for enforces the hard outer timeout.
        # The driver has its own timeout too (connect timeout=10s inside the driver).
        # This wait_for is a safety net in case the driver's timeout misfires.
        result = await asyncio.wait_for(
            driver_fn(config),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        return {
            "success":    False,
            "latency_ms": timeout_seconds * 1000,
            "category":   "TIMEOUT",
            "message":    f"Connection attempt timed out after {timeout_seconds} seconds.",
        }

    if result["success"]:
        return {"success": True, "latency_ms": result["latency_ms"]}

    # Classify the raw exception into a user-friendly category
    raw_error  = result.get("raw_error") or Exception("Unknown error")
    classified = _classify_error(config.get("engine", ""), raw_error)

    return {
        "success":    False,
        "latency_ms": result["latency_ms"],
        **classified,
    }
