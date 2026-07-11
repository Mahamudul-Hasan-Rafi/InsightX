from app.modules.nl_query.executors.base import ExecutionResult, SQLExecutor
from app.modules.nl_query.executors.delta import DeltaExecutor
from app.modules.nl_query.executors.oracle import OracleExecutor
from app.modules.nl_query.executors.postgresql import PostgreSQLExecutor

_REGISTRY: dict[str, type[SQLExecutor]] = {
    "postgresql": PostgreSQLExecutor,
    "oracle":     OracleExecutor,
    "delta":      DeltaExecutor,
}


def get_executor(engine: str) -> SQLExecutor:
    """Factory — return the right SQLExecutor for the given engine name."""
    klass = _REGISTRY.get(engine)
    if klass is None:
        raise NotImplementedError(
            f"No executor registered for engine '{engine}'. "
            f"Supported: {list(_REGISTRY)}"
        )
    return klass()


__all__ = ["get_executor", "SQLExecutor", "ExecutionResult"]
