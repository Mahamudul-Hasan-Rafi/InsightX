// web/config/engines.ts
//
// PURPOSE:
//   Frontend single source of truth for supported database engine metadata.
//   Drives engine card rendering, port auto-fill, auth method tabs, and TLS labels.
//
// BACKEND MIRROR:
//   api/app/config/engines_config.py is the backend equivalent.
//   When adding a new engine or auth method, update BOTH files.
//
// ADDING A NEW ENGINE:
//   1. Add its entry here (below)
//   2. Add its entry in api/app/config/engines_config.py
//   3. Add EngineType enum value in api/app/modules/datasources/schemas.py
//   4. Write a new driver in api/app/modules/datasources/drivers/
//   5. Register it in api/app/modules/datasources/connection_tester.py

export interface AuthMethodOption {
  value: string;
  label: string;
}

export interface TLSModeOption {
  value: string;
  label: string;
}

export interface EngineConfig {
  label: string; // Display name shown in the UI
  defaultPort: number; // Auto-filled in the port field on engine selection
  hasConnectionTypeToggle: boolean; // Oracle only: SID vs Service Name toggle
  authMethods: AuthMethodOption[];
  tls: {
    defaultMode: string;
    modes: TLSModeOption[];
  };
}

export const ENGINES: Record<string, EngineConfig> = {
  postgresql: {
    label: "PostgreSQL",
    defaultPort: 5432,
    hasConnectionTypeToggle: false,
    authMethods: [
      { value: "password", label: "Username & Password" },
      { value: "ldap", label: "LDAP" },
    ],
    tls: {
      defaultMode: "require",
      modes: [
        { value: "disable", label: "Disable" },
        { value: "allow", label: "Allow" },
        { value: "prefer", label: "Prefer" },
        { value: "require", label: "Require (recommended)" },
        { value: "verify-ca", label: "Verify CA" },
        { value: "verify-full", label: "Verify Full (strictest)" },
      ],
    },
  },

  oracle: {
    label: "Oracle 12c+",
    defaultPort: 1521,
    hasConnectionTypeToggle: true,
    authMethods: [
      { value: "password", label: "Username & Password" },
      { value: "wallet", label: "Oracle Wallet (.sso / .p12)" },
      { value: "kerberos", label: "Kerberos (requires Thick Mode)" },
    ],
    tls: {
      defaultMode: "ssl",
      modes: [{ value: "ssl", label: "SSL / TCPS" }],
    },
  },

  mssql: {
    label: "MS SQL Server",
    defaultPort: 1433,
    hasConnectionTypeToggle: false,
    authMethods: [
      { value: "password", label: "Username & Password" },
      { value: "windows", label: "Windows Authentication (NTLM)" },
      { value: "azure_ad", label: "Azure Active Directory" },
    ],
    tls: {
      defaultMode: "encrypt",
      modes: [{ value: "encrypt", label: "Encrypt (SSL/TLS)" }],
    },
  },

  delta: {
    label: "Delta Lakehouse (Spark)",
    defaultPort: 7077,
    hasConnectionTypeToggle: false,
    authMethods: [
      { value: "none", label: "No Authentication (Open Cluster)" },
    ],
    tls: {
      defaultMode: "",
      modes: [],
    },
  },
};

/** Engine display icons — used in engine cards and list page */
export const ENGINE_ICONS: Record<string, string> = {
  postgresql: "🐘",
  oracle: "🔶",
  mssql: "⚙️",
  delta: "🔺",
};
