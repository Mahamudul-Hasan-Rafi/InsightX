# api/app/core/config.py
#
# PURPOSE:
#   Typed application settings loaded from environment variables (or .env file).
#   pydantic-settings validates types and raises clear errors at startup if
#   required variables are missing — much better than bare os.getenv().
#
# USAGE IN OTHER MODULES:
#   from app.core.config import settings
#   print(settings.database_url)
#
# REQUIRED .env variables:
#   CREDENTIAL_ENCRYPTION_KEY — 64-character hex string
#
# OPTIONAL .env variables (defaults shown):
#   DATABASE_URL              — postgresql+asyncpg://insightx:insightx@localhost:5432/insightx_meta
#   SECURE_FILES_DIR          — ./secure-uploads
#   MAX_UPLOAD_SIZE_MB        — 5

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- InsightX metadata database ---
    # This is the INTERNAL database that stores datasource registrations.
    # It is NOT the same as the target databases users register through the UI.
    #
    # PostgreSQL is the expected metadata database. SQLite remains supported by
    # session.py only for explicit local/test DATABASE_URL overrides.
    database_url: str = "postgresql+asyncpg://insightx:insightx@localhost:5432/insightx_meta"

    # --- AES-256-GCM credential encryption key ---
    # 64 hex characters = 32 bytes = 256 bits.
    # REQUIRED — no default. The app will refuse to start without this.
    # Store in a secrets manager in production; never commit to version control.
    # Generate: python -c "import secrets; print(secrets.token_hex(32))"
    credential_encryption_key: str

    # --- Secure file storage ---
    # Stores TLS certs, Oracle Wallets, and Kerberos keytabs.
    # Must be outside the webroot and writable by the app process.
    # In Docker, mount this as a named persistent volume.
    secure_files_dir: str = "./secure-uploads"

    # --- Upload limits ---
    max_upload_size_mb: int = 5

    # --- Metadata DB pool settings ---
    # --- SQLAlchemy connection pooling ---
    # pool_size:
    #   Number of persistent metadata DB connections.
    # max_overflow:
    #   Temporary extra connections allowed during traffic spikes.
    # Total possible connections:
    #   pool_size + max_overflow
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # --- Keycloak / token introspection ---
    keycloak_url: str = ""
    keycloak_realm: str = "InsightX"
    keycloak_client_id: str = "InsightX"
    keycloak_client_secret: str = ""
    # --- Token Introspection Cache ---
    # Prevents excessive calls to Keycloak by caching token validation results for a short period.
    # Units: seconds
    introspect_cache_ttl_seconds: int = 30

    # --- BFF OAuth / session cookies ---
    # redirect_uri must be registered in Keycloak as a valid Redirect URI.
    redirect_uri: str = "http://10.11.200.109:8091/api/auth/callback"
    frontend_url: str = "http://10.11.200.109:5500"

    # Set cookie_secure=True in production (requires HTTPS).
    # SameSite=lax allows the Keycloak redirect (GET) to carry the state cookie.
    cookie_secure: bool = False
    cookie_samesite: str = "lax"

    # pydantic-settings v2: reads from .env file automatically
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


    # ── ─────────────────────────────────────────────────────────────────────
    #    M3 — Local Ollama LLM configuration
    # ── ─────────────────────────────────────────────────────────────────────
        # Pull the required models with:
    #   ollama pull nomic-embed-text     # embeddings
    #   ollama pull sqlcoder:7b          # SQL generation (best for text-to-SQL)
    #   ollama pull llama3.1:8b          # narrative generation
    #
    # Fallback SQL models (if sqlcoder is not available):
    #   ollama pull codellama:13b-instruct
    #   ollama pull mistral:7b-instruct
    #
    # nomic-embed-text produces 768-dimensional vectors.
    # The m3_table_embeddings.embedding column is vector(768).
    # If you change to a model with different dimensions you MUST drop and
    # recreate the m3_table_embeddings table.
    # ─────────────────────────────────────────────────────────────────────────

    # URL of the local Ollama server.
    ollama_base_url: str = "http://10.11.200.99:11434"

    # Model for generating vector embeddings of table descriptions.
    # nomic-embed-text: 768-dim, fast on CPU, purpose-built for retrieval.
    ollama_embed_model: str = "snowflake-arctic-embed2:latest"

    # Model for SQL generation (text-to-SQL).
    # sqlcoder:7b is the best local SQL model available on Ollama.
    # Benchmarks: Spider 83.7%, WikiSQL 93.1% (defog/sqlcoder-7b-2).
    # Fallback: codellama:13b-instruct
    ollama_sql_model: str = "qwen3-coder:30b"

    # Model for generating plain-English narrative of query results.
    ollama_narrative_model: str = "llama3.1:8b"

    # HTTP timeout for Ollama calls (seconds).
    # Local 7B models on consumer GPUs take ~10-30s.
    # 13B models may need 60-120s on CPU.
    ollama_timeout_seconds: int = 120

    # ── M3 query execution safety limits ─────────────────────────────────────
    #
    # These protect production databases from runaway queries.

    # Max rows returned to the frontend from any single NL query.
    nl_query_max_result_rows: int = 2000

    # PostgreSQL statement_timeout for NL query execution (seconds).
    nl_query_statement_timeout_seconds: int = 30

    # asyncpg connection timeout (seconds).
    nl_query_connection_timeout_seconds: int = 10

    # ── ─────────────────────────────────────────────────────────────────────
    #    M4 — Delta Lakehouse (Spark) configuration
    # ── ─────────────────────────────────────────────────────────────────────
    #
    # These describe the LOCAL machine running this backend process — NOT the
    # remote Spark cluster (which is configured per-datasource: master host/port
    # + HDFS namenode). Spark 3.5.x requires Java 8/11/17 (NOT 21+), and PyPI's
    # pyspark only ships Scala 2.12 JARs, so a separately-downloaded Scala 2.13
    # build is used via SPARK_HOME when the cluster runs Scala 2.13.

    # Overrides JAVA_HOME before the first `import pyspark`. Leave empty to use
    # whatever JAVA_HOME is already set in the process environment.
    spark_java_home: str = ""

    # Overrides SPARK_HOME so PySpark uses this distribution's JARs instead of
    # the ones bundled with the pip package. Leave empty to use the pip-bundled JARs.
    spark_home_override: str = ""

    # Maven coordinate for the Delta Lake Spark connector, resolved at runtime
    # via spark.jars.packages (first resolution downloads and caches the JARs).
    spark_delta_package: str = "io.delta:delta-spark_2.13:3.2.1"

    # Connection-test timeout override for the 'delta' engine (seconds).
    # Cold JVM boot + first-time package resolution can take 30-90s, far
    # longer than the 10s default used for the RDBMS engines.
    spark_connection_timeout_seconds: int = 90

@lru_cache()
def get_settings() -> Settings:
    """
    Returns a cached Settings instance.
    lru_cache ensures the .env file is only read once per process lifecycle.
    Cached — calling this function 100 times is as fast as calling it once.
    """
    return Settings()


# Module-level alias — other modules do: `from app.core.config import settings`
settings = get_settings()
