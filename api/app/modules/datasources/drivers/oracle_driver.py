# api/app/modules/datasources/drivers/oracle_driver.py
#
# PURPOSE:
#   Adapter that wraps python-oracledb to test Oracle 12c+ connections.
#   Uses the async API (oracledb.connect_async) available in Thin Mode.
#
# LIBRARY: python-oracledb (pip install python-oracledb)
#   Successor to cx_Oracle. Thin Mode does not require Oracle Client libraries.
#
# THIN MODE vs THICK MODE:
#   Thin Mode (default in python-oracledb v1.0+):
#     - No Oracle Instant Client required — works out of the box.
#     - Supports: password auth, Oracle Wallet (SSL + auto-login), basic TCPS/SSL,
#                 async API (oracledb.connect_async).
#     - Does NOT support: Kerberos (requires krb5 + Thick Mode).
#
#   Thick Mode (optional):
#     - Requires Oracle Instant Client installed on the host.
#     - Call oracledb.init_oracle_client(lib_dir="...") in main.py ONCE at startup.
#     - Required for: Kerberos, DRCP, advanced Oracle-specific features.
#     - If Kerberos is needed, add init_oracle_client() to main.py's lifespan().
#
# CONNECT STRING FORMATS:
#   Service Name (Easy Connect): "host:port/service_name"
#   SID (legacy):  "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=h)(PORT=p))(CONNECT_DATA=(SID=s)))"
#   TCPS (SSL):    "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCPS)...)(SECURITY=(SSL_SERVER_DN_MATCH=...)))"

import time
import oracledb


def _build_connect_string(config: dict) -> str:
    """
    Builds the Oracle DSN/connect string based on connection type and TLS config.

    Returns an Easy Connect string (for service names), a legacy SID descriptor,
    or a TCPS (Oracle SSL) descriptor when TLS is enabled.

    Args:
        config: Datasource config dict

    Returns:
        Oracle DSN string
    """
    host        = config["host"]
    port        = int(config["port"])
    database    = config["database"]   # SID or Service Name
    conn_type   = config.get("oracle_connection_type", "service_name")
    tls         = config.get("tls") or {}

    if tls.get("enabled"):
        # TCPS: Oracle's SSL mode. Must use PROTOCOL=TCPS in the ADDRESS.
        # SSL_SERVER_DN_MATCH controls server certificate verification.
        dn_match = "YES" if tls.get("verify_server_cert", True) else "NO"
        return (
            f"(DESCRIPTION="
            f"(ADDRESS=(PROTOCOL=TCPS)(HOST={host})(PORT={port}))"
            f"(CONNECT_DATA=(SERVICE_NAME={database}))"
            f"(SECURITY=(SSL_SERVER_DN_MATCH={dn_match}))"
            f")"
        )

    if conn_type == "sid":
        # Legacy SID format — required for some older Oracle 11g/12c configurations
        return (
            f"(DESCRIPTION="
            f"(ADDRESS=(PROTOCOL=TCP)(HOST={host})(PORT={port}))"
            f"(CONNECT_DATA=(SID={database}))"
            f")"
        )

    # Easy Connect (Service Name) — recommended for Oracle 12c+
    return f"{host}:{port}/{database}"


async def test_oracle_connection(config: dict) -> dict:
    """
    Tests an Oracle database connection using python-oracledb's async API.
    Non-destructive — only SELECT 1 FROM DUAL is executed.

    Args:
        config: Connection config dict.
                Keys: host, port, database, oracle_connection_type,
                auth_method, credentials, tls (optional)

    Returns:
        {"success": bool, "latency_ms": int}
        On failure: adds "raw_error": Exception for classification
    """
    auth_method    = config["auth_method"]
    credentials    = config["credentials"]
    connect_string = _build_connect_string(config)

    start_ms   = int(time.time() * 1000)
    connection = None

    try:
        if auth_method == "password":
            connection = await oracledb.connect_async(
                user               = credentials["username"],
                password           = credentials["password"],
                dsn                = connect_string,
                tcp_connect_timeout = 10,   # seconds
            )

        elif auth_method == "wallet":
            # Oracle Wallet: wallet_location is the server-side DIRECTORY path
            # containing cwallet.sso (auto-login) or ewallet.p12 (password-protected).
            # wallet_password is only needed for ewallet.p12 format.
            connection = await oracledb.connect_async(
                dsn                = connect_string,
                wallet_location    = credentials["wallet_location"],
                wallet_password    = credentials.get("wallet_password"),
                user               = credentials.get("username") or None,
                tcp_connect_timeout = 10,
            )

        elif auth_method == "kerberos":
            # Kerberos requires Thick Mode (Oracle Instant Client + krb5 libraries).
            # In Thin Mode (the default here), python-oracledb will raise an exception
            # containing "not supported in thin mode", which connection_tester.py
            # classifies as UNSUPPORTED_CONFIG — a clear, actionable error for the user.
            #
            # TO ENABLE KERBEROS:
            #   1. Install Oracle Instant Client on the server
            #   2. Call oracledb.init_oracle_client(lib_dir="/path/to/instantclient") in main.py
            #   3. Ensure the `kinit` TGT is valid for the Kerberos principal
            connection = await oracledb.connect_async(
                user               = f"/{credentials['principal']}",
                dsn                = connect_string,
                externalauth       = True,
                tcp_connect_timeout = 10,
            )

        else:
            raise ValueError(f"Unsupported Oracle auth method: {auth_method}")

        # DUAL is Oracle's built-in single-row, single-column utility table —
        # the canonical Oracle equivalent of PostgreSQL's SELECT 1
        cursor = connection.cursor()
        try:
            await cursor.execute("SELECT 1 FROM DUAL")
            await cursor.fetchone()
        finally:
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
        if connection:
            try:
                await connection.close()
            except Exception:
                pass   # Ignore cleanup errors
