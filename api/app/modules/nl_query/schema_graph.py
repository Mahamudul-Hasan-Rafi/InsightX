"""
api/app/modules/nl_query/schema_graph.py
─────────────────────────────────────────
Apache AGE property-graph integration for multi-hop join path discovery.

WHY APACHE AGE?
  Finding the shortest join path between two tables requires traversing a
  graph: each table is a node, each FK relationship is an edge.  For simple
  one-hop cases a SQL join on table_relationships suffices.  For multi-hop
  (orders → order_lines → products → categories) a graph traversal is cleaner
  and more general than recursive CTEs.

WHY NOT ALWAYS USE AGE?
  AGE requires:
    1.  The `age` extension installed in PostgreSQL
    2.  `LOAD 'age'` executed at session start (every connection)
    3.  psycopg2 (not asyncpg) — AGE returns `agtype` which asyncpg can't
        deserialise natively
  If any of these fail we fall back gracefully to a direct query on
  `table_relationships` which already holds the same data.

INTEGRATION:
  This module is called by context_builder.py.  The caller wraps the
  synchronous functions with asyncio.get_event_loop().run_in_executor()
  so they don't block the FastAPI event loop.

GRAPH SCHEMA:
  Nodes:  (:Table {name, schema_name, datasource_id, tenant_id})
  Edges:  [:JOINS_ON {from_column, to_column, relationship_type, is_discovered}]

CYPHER QUERIES (Apache AGE Cypher subset):
  MERGE is used for upserts — idempotent, safe to call repeatedly.
  shortestPath is used for join path lookup.
"""

import json
import logging
import re
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# ── AGE graph name (must match the one in the migration) ───────────────────
GRAPH_NAME = "insightx_schema_graph"


# ──────────────────────────────────────────────────────────────────────────────
# Safe identifier validation
# ──────────────────────────────────────────────────────────────────────────────

_SAFE_IDENT = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_$.]*$')


def _safe(name: str) -> str:
    """
    Validate that an identifier is safe to interpolate into a Cypher query.
    AGE does not support parameterised Cypher so we must string-interpolate —
    this guard prevents injection via table or column names.

    Raises ValueError on suspicious input.
    """
    if not _SAFE_IDENT.match(name):
        raise ValueError(f"Unsafe identifier for AGE query: {name!r}")
    return name


# ──────────────────────────────────────────────────────────────────────────────
# Connection factory
# ──────────────────────────────────────────────────────────────────────────────

def _make_age_conn(database_url: str):
    """
    Open a psycopg2 connection configured for Apache AGE.

    Replaces the asyncpg scheme so psycopg2 can parse the URL, loads the AGE
    extension, and sets the required search_path.

    Returns the connection or raises RuntimeError if AGE is not available.
    """
    import psycopg2
    import psycopg2.extras

    # Convert SQLAlchemy async URL to plain psycopg2 DSN.
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")

    conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = False

    with conn.cursor() as cur:
        try:
            cur.execute("LOAD 'age';")
            cur.execute("SET search_path = ag_catalog, \"$user\", public;")
        except Exception as e:
            conn.close()
            raise RuntimeError(f"AGE not available on this PostgreSQL instance: {e}") from e
    return conn


# ──────────────────────────────────────────────────────────────────────────────
# Graph building (called from context_builder.index_schema)
# ──────────────────────────────────────────────────────────────────────────────

def build_schema_graph_sync(
    database_url:  str,
    relationships: list[dict],
    datasource_id: str,
    tenant_id:     str,
) -> dict:
    """
    Rebuild all Table nodes and JOINS_ON edges for a datasource+tenant.

    Called from an executor thread (not async-safe).
    Returns {"nodes": int, "edges": int} on success.
    Raises RuntimeError if AGE is not available — caller should fall back.

    `relationships` is the list from table_relationships joined with
    schema_name, shaped as:
        [{from_table, from_column, to_table, to_column,
          relationship_type, schema_name}, ...]
    """
    conn = _make_age_conn(database_url)
    nodes = 0
    edges = 0

    try:
        with conn.cursor() as cur:
            for rel in relationships:
                schema    = _safe(rel["schema_name"])
                from_tbl  = _safe(rel["from_table"])
                to_tbl    = _safe(rel["to_table"])
                from_col  = _safe(rel["from_column"])
                to_col    = _safe(rel["to_column"])
                rel_type  = rel.get("relationship_type", "many-to-one")
                ds_id     = str(datasource_id)
                t_id      = str(tenant_id)

                # Upsert FROM node
                cur.execute(_cypher(f"""
                    MERGE (t:Table {{
                        name:          '{from_tbl}',
                        schema_name:   '{schema}',
                        datasource_id: '{ds_id}',
                        tenant_id:     '{t_id}'
                    }})
                    RETURN t
                """))
                nodes += 1

                # Upsert TO node
                cur.execute(_cypher(f"""
                    MERGE (t:Table {{
                        name:          '{to_tbl}',
                        schema_name:   '{schema}',
                        datasource_id: '{ds_id}',
                        tenant_id:     '{t_id}'
                    }})
                    RETURN t
                """))
                nodes += 1

                # Upsert edge (directed: from_table → to_table)
                cur.execute(_cypher(f"""
                    MATCH (a:Table {{name: '{from_tbl}', datasource_id: '{ds_id}'}}),
                          (b:Table {{name: '{to_tbl}',   datasource_id: '{ds_id}'}})
                    MERGE (a)-[e:JOINS_ON {{
                        from_column:       '{from_col}',
                        to_column:         '{to_col}',
                        relationship_type: '{rel_type}'
                    }}]->(b)
                    RETURN e
                """))
                edges += 1

        conn.commit()
        logger.info("AGE graph built: %d node upserts, %d edge upserts.", nodes, edges)
        return {"nodes": nodes, "edges": edges}

    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Join path lookup
# ──────────────────────────────────────────────────────────────────────────────

def find_join_paths_sync(
    database_url:  str,
    datasource_id: str,
    table_names:   list[str],
    max_hops:      int = 3,
) -> list[dict]:
    """
    Find shortest join paths between every pair of selected tables.

    Returns a list of join edge dicts:
        [{from_table, from_column, to_table, to_column, relationship_type}]

    Duplicate pairs (A→B and B→A) are deduplicated automatically since
    we use a sorted pair key.

    Raises RuntimeError if AGE unavailable (caller falls back to SQL).
    """
    conn = _make_age_conn(database_url)
    paths: list[dict] = []
    seen_pairs: set[tuple] = set()
    ds_id = str(datasource_id)

    try:
        with conn.cursor() as cur:
            for i, t1 in enumerate(table_names):
                for t2 in table_names[i + 1:]:
                    pair = tuple(sorted([t1, t2]))
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)

                    try:
                        _t1 = _safe(t1)
                        _t2 = _safe(t2)
                    except ValueError:
                        logger.warning("Skipping unsafe table name in AGE query: %s / %s", t1, t2)
                        continue

                    cypher = f"""
                        MATCH (a:Table {{name: '{_t1}', datasource_id: '{ds_id}'}}),
                              (b:Table {{name: '{_t2}', datasource_id: '{ds_id}'}})
                        MATCH path = shortestPath(
                            (a)-[:JOINS_ON*1..{max_hops}]-(b)
                        )
                        RETURN relationships(path)
                    """
                    try:
                        cur.execute(_cypher(cypher, "rels agtype"))
                        row = cur.fetchone()
                        if row:
                            edges = _parse_agtype_relationships(row.get("rels") or row.get(0, ""))
                            paths.extend(edges)
                    except Exception as e:
                        # No path between these two tables — completely normal.
                        logger.debug("No AGE path between %s and %s: %s", t1, t2, e)
                        conn.rollback()   # reset after per-query error
                        # Re-load AGE after rollback
                        with conn.cursor() as reset_cur:
                            reset_cur.execute("LOAD 'age';")
                            reset_cur.execute("SET search_path = ag_catalog, \"$user\", public;")
                        conn.commit()

        conn.commit()
        return paths
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _cypher(query: str, columns: str = "result agtype") -> str:
    """
    Wrap a Cypher query in the AGE cypher() SQL function.

    AGE syntax:
        SELECT * FROM ag_catalog.cypher('graph', $$ CYPHER $$) AS (col agtype);

    $cypher$ quoting avoids conflicts with single quotes inside the Cypher.
    """
    return (
        f"SELECT * FROM ag_catalog.cypher("
        f"    '{GRAPH_NAME}',"
        f"    $cypher${query}$cypher$"
        f") AS ({columns});"
    )


def _parse_agtype_relationships(agtype_str: str) -> list[dict]:
    """
    Parse the agtype string returned by AGE's relationships() function.

    AGE returns agtype which looks like JSON but with trailing type annotations
    like `"orders"::text`.  We strip annotations and parse as JSON.

    Returns a list of dicts with keys: from_table, from_column, to_table,
    to_column, relationship_type.
    """
    if not agtype_str:
        return []

    results = []
    raw = str(agtype_str)

    # Find all property objects in the agtype output.
    prop_pattern = re.compile(r'"properties"\s*:\s*(\{[^}]+\})', re.DOTALL)
    for match in prop_pattern.finditer(raw):
        try:
            props_str = match.group(1)
            # Remove AGE type annotations (e.g. "value"::text)
            props_str = re.sub(r'::\w+', '', props_str)
            props = json.loads(props_str)

            edge = {
                "from_column":       props.get("from_column",       ""),
                "to_column":         props.get("to_column",         ""),
                "relationship_type": props.get("relationship_type", "many-to-one"),
            }
            if edge["from_column"] and edge["to_column"]:
                results.append(edge)
        except (json.JSONDecodeError, KeyError) as e:
            logger.debug("AGE agtype parse warning: %s", e)

    return results
