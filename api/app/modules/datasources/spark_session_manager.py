# api/app/modules/datasources/spark_session_manager.py
#
# PURPOSE:
#   Singleton SparkSession manager for the Delta Lakehouse engine, shared by
#   the connection-test driver, the schema browser, and the M3 NL-to-SQL
#   executor — anything that needs to talk to a Spark/Delta cluster.
#
# WHY A SINGLETON:
#   Creating a SparkSession registers a new Spark application against the
#   cluster master — a slow (JVM boot + first-time Delta package resolution
#   can take 30-90s) and stateful operation. Tearing one down after every
#   connection test / schema browse / NL query would be both slow and would
#   leave orphaned Spark applications registered on the cluster. Instead one
#   SparkSession per (master_url, hdfs_namenode) pair is cached and reused,
#   mirroring the "reuse existing SparkSession" pattern used interactively.
#
# WHY JAVA_HOME/SPARK_HOME MUST BE SET BEFORE THE FIRST `import pyspark`:
#   PySpark resolves its JVM launcher and JAR classpath at import time from
#   the process environment. Setting these env vars after import has no
#   effect, so pyspark is only ever imported inside functions here — never
#   at module scope — after _configure_environment() has run.

import asyncio
import logging
import os
import socket
import sys
import threading
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_sessions: dict[tuple[str, str], Any] = {}   # (master_url, hdfs_namenode) -> SparkSession
_env_configured = False


def _configure_environment() -> None:
    """Apply JAVA_HOME/SPARK_HOME overrides exactly once, before any pyspark import."""
    global _env_configured
    if _env_configured:
        return
    if settings.spark_java_home:
        os.environ["JAVA_HOME"] = settings.spark_java_home
    if settings.spark_home_override:
        os.environ["SPARK_HOME"] = settings.spark_home_override
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    _env_configured = True


def _get_local_ip(remote_host: str, remote_port: int) -> str:
    """
    Determine this machine's outbound IP for the route that reaches
    remote_host — used as spark.driver.host so cluster executors can call
    back into this process. UDP connect() only resolves a route locally; it
    sends no packets.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((remote_host, remote_port))
        return s.getsockname()[0]
    finally:
        s.close()


def get_warehouse_dir(config: dict) -> str:
    """
    Compute the effective warehouse dir for a Delta datasource config — the
    same default used when building the SparkSession. Exposed so callers
    that need to inspect HDFS directly (e.g. schema_inspector's table
    auto-discovery) don't have to duplicate this default.
    """
    creds = config.get("credentials", {})
    hdfs_namenode = creds["hdfs_namenode"]
    return creds.get("warehouse_dir") or f"{hdfs_namenode.rstrip('/')}/user/spark/warehouse"


def _build_session(config: dict) -> Any:
    """Blocking — must only be called via run_in_executor."""
    _configure_environment()

    from pyspark.sql import SparkSession  # local import: env vars must already be set

    host = config["host"]
    port = int(config["port"])
    creds = config.get("credentials", {})
    hdfs_namenode = creds["hdfs_namenode"]
    warehouse_dir = get_warehouse_dir(config)

    namenode_parsed = urlparse(hdfs_namenode)
    namenode_host = namenode_parsed.hostname or hdfs_namenode
    namenode_port = namenode_parsed.port or 9000

    existing = SparkSession.getActiveSession()
    if existing is not None:
        return existing

    driver_host = _get_local_ip(namenode_host, namenode_port)

    logger.info("Building SparkSession: master=spark://%s:%s hdfs=%s", host, port, hdfs_namenode)

    spark = (
        SparkSession.builder
        .master(f"spark://{host}:{port}")
        .appName("InsightX Delta Lakehouse")
        .config("spark.driver.host", driver_host)
        .config("spark.driver.bindAddress", "0.0.0.0")
        # Pinned (not left dynamic): executors make an INBOUND connection back
        # to the driver for heartbeats and block/shuffle transfer. Without a
        # fixed port there's nothing stable to open a firewall rule for on the
        # driver machine, so those inbound connections get silently blocked —
        # surfacing as repeated "Remote RPC client disassociated" / lost
        # executor errors. Safe to pin since this product only ever runs one
        # Spark connection at a time (see the Data Source page's own copy).
        .config("spark.driver.port", "4040")
        .config("spark.driver.blockManager.port", "4041")
        # Spark's web UI ALSO defaults to port 4040 — a separate config key
        # (spark.ui.port) from spark.driver.port above, easy to miss. Left
        # enabled, it grabs 4040 right after the driver's RPC endpoint does,
        # falls back to 4041, and collides with the pinned blockManager port,
        # bumping THAT to an unpredictable port too — silently defeating the
        # whole point of pinning ports for a firewall rule. InsightX is a
        # backend API, not an interactive notebook, so the UI is disabled
        # outright rather than given yet another port to pin.
        .config("spark.ui.enabled", "false")
        .config("spark.driver.memory", "4g")
        .config("spark.executor.memory", "3g")
        .config("spark.executor.cores", "4")
        .config("spark.cores.max", "8")
        .config("spark.executor.heartbeatInterval", "60s")
        .config("spark.network.timeout", "300s")
        .config("spark.hadoop.fs.defaultFS", hdfs_namenode)
        .config("spark.hadoop.dfs.client.use.datanode.hostname", "false")
        .config("spark.hadoop.dfs.datanode.use.datanode.hostname", "false")
        .config("spark.sql.warehouse.dir", warehouse_dir)
        .config("spark.jars.packages", settings.spark_delta_package)
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config("spark.pyspark.python", "python3")
        .config("spark.pyspark.driver.python", sys.executable)
        .getOrCreate()
    )

    spark.sparkContext._jsc.hadoopConfiguration().set("fs.defaultFS", hdfs_namenode)
    spark.sparkContext._jsc.hadoopConfiguration().set("dfs.client.use.datanode.hostname", "false")

    return spark


def _get_or_create_sync(config: dict) -> Any:
    creds = config.get("credentials", {})
    hdfs_namenode = creds.get("hdfs_namenode", "")
    key = (f"{config['host']}:{config['port']}", hdfs_namenode)

    with _lock:
        session = _sessions.get(key)
        if session is not None:
            return session
        session = _build_session(config)
        _sessions[key] = session
        return session


async def get_or_create_spark_session(config: dict) -> Any:
    """Async entry point — get or build the cached SparkSession off the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_or_create_sync, config)


async def run_spark(fn, *args) -> Any:
    """Run a blocking Spark call (e.g. spark.sql(...).collect) off the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fn, *args)
