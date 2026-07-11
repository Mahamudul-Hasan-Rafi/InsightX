"""
executors/base.py
──────────────────
Abstract base for SQL executors (Strategy pattern).

Each engine (PostgreSQL, Oracle, MSSQL, MySQL) implements SQLExecutor and
handles its own connection, row-limit syntax, type serialisation, and
engine-specific quirks.

The factory function get_executor() in __init__.py selects the right class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionResult:
    """Normalised result returned by every executor."""
    columns:   list[str]
    rows:      list[list[Any]]
    row_count: int
    exec_ms:   int


class SQLExecutor(ABC):
    """
    Abstract executor — Strategy interface.
    
    Implementors must override execute() with engine-specific connection and
    query logic.  The config dict comes from get_datasource_runtime_config()
    and contains: engine, host, port, database, credentials, tls, schema_name.
    """

    @abstractmethod
    async def execute(
        self,
        config:   dict[str, Any],
        sql:      str,
        max_rows: int = 10_000,
    ) -> ExecutionResult:
        """Execute sql against the target and return an ExecutionResult."""
        ...

    # ── Shared helpers available to all subclasses ────────────────────────────

    @staticmethod
    def _serialize_row(row: Any) -> list[Any]:
        """Convert a DB driver row to a plain JSON-serialisable list."""
        import uuid as _uuid_mod
        from datetime import date, datetime
        from decimal import Decimal

        result = []
        for v in row:
            if isinstance(v, (datetime, date)):
                result.append(v.isoformat())
            elif isinstance(v, Decimal):
                result.append(float(v))
            elif isinstance(v, _uuid_mod.UUID):
                result.append(str(v))
            elif isinstance(v, (bytes, bytearray, memoryview)):
                result.append("<binary>")
            else:
                result.append(v)
        return result
