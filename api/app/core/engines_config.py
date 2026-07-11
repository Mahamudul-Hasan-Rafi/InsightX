# api/app/core/engines_config.py
#
# Backend source of truth for datasource engine capabilities.

from typing import TypedDict


class EngineCapabilities(TypedDict):
    supported_auth_methods: list[str]


ENGINES: dict[str, EngineCapabilities] = {
    "postgresql": {
        "supported_auth_methods": ["password", "ldap"],
    },
    "oracle": {
        "supported_auth_methods": ["password", "wallet", "kerberos"],
    },
    "mssql": {
        "supported_auth_methods": ["password", "windows", "azure_ad"],
    },
    "delta": {
        "supported_auth_methods": ["none"],
    },
}
