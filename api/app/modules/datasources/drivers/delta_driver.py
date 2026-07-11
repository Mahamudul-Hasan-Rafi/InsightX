# api/app/modules/datasources/drivers/delta_driver.py
#
# PURPOSE:
#   Tests connectivity to a Delta Lakehouse (Spark standalone cluster + HDFS).
#   Unlike the RDBMS drivers, this can't be a cheap "SELECT 1" — it gets/builds
#   the cached SparkSession (see spark_session_manager.py) and runs
#   CREATE DATABASE IF NOT EXISTS spark_catalog.<database>, which exercises
#   master connectivity, HDFS reachability, and Delta extension loading in one
#   shot, AND provisions the target database if it doesn't already exist —
#   idempotent, safe to re-run on every test/re-test.
#
# WHY CREATE DATABASE INSTEAD OF SHOW DATABASES:
#   SHOW DATABASES only proves the session can talk to the metastore — it
#   doesn't confirm the *specific* database the user configured is usable.
#   Spark's catalog hierarchy is catalog.database.table (spark_catalog is the
#   fixed default catalog; "database" here is the actual Hive/Delta schema,
#   e.g. "ekyc_db"). CREATE DATABASE IF NOT EXISTS both validates and
#   provisions that exact target in a single non-destructive call.
#
# LATENCY NOTE:
#   First call in the process pays the full SparkSession cold-start cost
#   (JVM boot + Delta package resolution, 30-90s). connection_tester.py grants
#   'delta' a longer timeout than the RDBMS engines for this reason. Every
#   call after the first reuses the cached session and is fast.

import re
import time

from app.modules.datasources.spark_session_manager import get_or_create_spark_session, run_spark

# Guards against SQL injection via config["database"], which is interpolated
# directly into DDL text — spark.sql() has no parameterised-identifier form.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")


async def test_delta_connection(config: dict) -> dict:
    """
    Tests a Delta Lakehouse connection by building/reusing a SparkSession and
    running CREATE DATABASE IF NOT EXISTS spark_catalog.<database>.

    Args:
        config: Connection config dict.
                Keys: host (Spark master host), port (Spark master port),
                database (target Hive/Delta database name, e.g. "ekyc_db"),
                credentials {method: "none", hdfs_namenode, warehouse_dir?}

    Returns:
        {"success": bool, "latency_ms": int}
        On failure: adds "raw_error": Exception for classification in connection_tester.py
    """
    start_ms = int(time.time() * 1000)
    database = config.get("database", "")

    try:
        if not _IDENTIFIER_RE.match(database):
            raise ValueError(
                f"'{database}' is not a valid database name: letters, digits, and "
                "underscores only, starting with a letter."
            )

        spark = await get_or_create_spark_session(config)
        await run_spark(
            lambda: spark.sql(f"CREATE DATABASE IF NOT EXISTS spark_catalog.{database}").collect()
        )

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
