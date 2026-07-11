'use client';

import { useState, useEffect } from 'react';
import useSWR from 'swr';
import useSWRMutation from 'swr/mutation';
import Icon from '@/app/component/Icon';
import DBLogo from '@/app/component/DBLogo';
import { DB_TYPES } from '@/lib/dummy-data';
import type { DbType } from '@/lib/types';
import {
  getDatasources,
  postDatasource,
  postDatasourceTest,
  postDatasourceUpload,
  postDatasourceRetest,
  getDatasourceTables,
  getDatasourceSearch,
  deleteDatasourceUrl,
  patchDatasourceDeactivate,
  getDatasourceColumns,
  getTableAnnotations,
  putTableAnnotations,
  getSchemaRelationships,
  postSchemaRelationship,
  deleteSchemaRelationship,
  postSyncRelationships,
} from '@/config/url.config';

import { toast } from '@/lib/utils/toast.utils';
import type {
  ColumnMeta,
  ColumnMetaListResult,
  ColumnAnnotationItem,
  TableAnnotationPutPayload,
  TableAnnotationResult,
  Relationship,
  RelationshipCreatePayload,
  RelationshipListResult,
  RelationshipType,
} from '@/lib/types/interface/features/annotation.interface';
import { get, post, put, patch, del } from '@/lib/utils/fetch.utils';
import type {
  DatasourceRecord,
  DatasourcePayload,
  DatasourceListResponse,
  FileUploadResponse,
  TestConnectionResult,
  EngineType,
  AuthMethod,
  SchemaObject,
  TableBrowseResult,
  SearchTableResult,
} from '@/lib/types/interface/features/datasource.interface';
import { ENGINES } from '@/config/engines';
import ConfirmModal from '@/app/component/modal/ConfirmModal';

/* ─── Engine metadata helpers ─── */

const TO_ENGINE: Record<string, EngineType> = {
  oracle: 'oracle',
  postgres: 'postgresql',
  sqlserver: 'mssql',
  delta: 'delta',
};

const ENGINE_DISPLAY: Record<
  EngineType,
  { slug: string; letter: string; color: string; label: string }
> = {
  oracle: {
    slug: 'oracle',
    letter: 'O',
    color: 'oklch(0.6 0.17 28)',
    label: 'Oracle DB',
  },
  postgresql: {
    slug: 'postgres',
    letter: 'P',
    color: 'oklch(0.55 0.13 250)',
    label: 'PostgreSQL',
  },
  mssql: {
    slug: 'sqlserver',
    letter: 'S',
    color: 'oklch(0.58 0.15 18)',
    label: 'SQL Server',
  },
  delta: {
    slug: 'delta',
    letter: 'D',
    color: 'oklch(0.62 0.14 200)',
    label: 'Delta Lakehouse',
  },
};

const DEFAULT_TLS_MODE: Record<EngineType, string> = {
  oracle: 'ssl',
  postgresql: 'require',
  mssql: 'encrypt',
  delta: '',
};

/* ─── Credential Modal ─── */

function CredentialModal({
  db,
  onClose,
  onCreated,
}: {
  db: DbType;
  onClose: () => void;
  onCreated: () => void;
}) {
  const engineId = (TO_ENGINE[db.id] ?? 'postgresql') as EngineType;
  const engineConfig = ENGINES[engineId] ?? ENGINES['postgresql'];
  const availableAuthMethods = engineConfig.authMethods;
  const tlsModes = engineConfig.tls?.modes ?? [];

  // Connection fields
  const [name, setName] = useState('');
  const [host, setHost] = useState('');
  const [port, setPort] = useState(db.port);
  const [database, setDatabase] = useState('');
  const [defaultSchema, setDefaultSchema] = useState('');
  const [oracleConnType, setOracleConnType] = useState<'service_name' | 'sid'>(
    'service_name',
  );

  // Auth
  const [authMethod, setAuthMethod] = useState<string>(
    availableAuthMethods[0]?.value ?? 'password',
  );
  const [authCredentials, setAuthCredentials] = useState<
    Record<string, unknown>
  >({});

  // TLS (not applicable to Delta — open Spark standalone cluster, no cert flow in this pass)
  const [useTls, setUseTls] = useState(engineId !== 'delta');
  const [tlsMode, setTlsMode] = useState(
    engineConfig.tls?.defaultMode ?? DEFAULT_TLS_MODE[engineId] ?? '',
  );
  const [tlsVerify, setTlsVerify] = useState(true);
  const [caCertFile, setCaCertFile] = useState<File | null>(null);
  const [clientCertFile, setClientCertFile] = useState<File | null>(null);
  const [clientKeyFile, setClientKeyFile] = useState<File | null>(null);

  // Test / save state
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    success: boolean;
    latency_ms?: number;
    message?: string;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const [resolvedCredentials, setResolvedCredentials] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [resolvedTlsExtra, setResolvedTlsExtra] = useState<
    Record<string, string | null>
  >({});

  // API mutations — imperative triggers (useSWRMutation) over the common fetcher.
  const { trigger: triggerUpload } = useSWRMutation(
    postDatasourceUpload,
    (
      url: string,
      {
        arg,
      }: {
        arg: {
          file: File;
          type: 'ca_cert' | 'client_cert' | 'client_key' | 'wallet' | 'keytab';
        };
      },
    ) => {
      const form = new FormData();
      form.append('file', arg.file);
      form.append('type', arg.type);
      return post<FileUploadResponse, FormData>(url, form);
    },
  );
  const { trigger: triggerTest } = useSWRMutation(
    postDatasourceTest,
    (url: string, { arg }: { arg: DatasourcePayload }) =>
      post<TestConnectionResult, DatasourcePayload>(url, arg),
  );
  const { trigger: triggerCreate } = useSWRMutation(
    postDatasource,
    (url: string, { arg }: { arg: DatasourcePayload }) =>
      post<DatasourceRecord, DatasourcePayload>(url, arg),
  );

  function resetTest() {
    setTestResult(null);
    setResolvedCredentials(null);
    setResolvedTlsExtra({});
  }

  // Upload any File objects before test/save; returns clean credential +
  // TLS extra objects with server-side paths substituted in.
  async function resolveUploads(): Promise<{
    credentials: Record<string, unknown>;
    tlsExtra: Record<string, string | null>;
  }> {
    const creds: Record<string, unknown> = { ...authCredentials };
    const tlsExtra: Record<string, string | null> = {};

    if (creds.walletFile instanceof File) {
      const res = await triggerUpload({ file: creds.walletFile, type: 'wallet' });
      creds.walletLocation = res?.path;
      delete creds.walletFile;
      delete creds.walletFileName;
    }
    if (creds.keytabFile instanceof File) {
      const res = await triggerUpload({ file: creds.keytabFile, type: 'keytab' });
      creds.keytabPath = res?.path;
      delete creds.keytabFile;
      delete creds.keytabFileName;
    }
    if (caCertFile) {
      const res = await triggerUpload({ file: caCertFile, type: 'ca_cert' });
      tlsExtra.ca_cert_path = res?.path ?? null;
    }
    if (clientCertFile) {
      const res = await triggerUpload({ file: clientCertFile, type: 'client_cert' });
      tlsExtra.client_cert_path = res?.path ?? null;
    }
    if (clientKeyFile) {
      const res = await triggerUpload({ file: clientKeyFile, type: 'client_key' });
      tlsExtra.client_key_path = res?.path ?? null;
    }

    return { credentials: creds, tlsExtra };
  }

  function buildPayload(
    credentials: Record<string, unknown> = authCredentials,
    tlsExtra: Record<string, string | null> = {},
  ): DatasourcePayload {
    return {
      name,
      engine: engineId,
      host,
      port: parseInt(String(port), 10),
      database,
      oracle_connection_type:
        engineId === 'oracle' ? oracleConnType : undefined,
      default_schema: defaultSchema.trim(),
      auth_method: authMethod as AuthMethod,
      credentials,
      tls: {
        enabled: useTls,
        mode: tlsMode,
        verify_server_cert: tlsVerify,
        ca_cert_path: tlsExtra.ca_cert_path ?? null,
        client_cert_path: tlsExtra.client_cert_path ?? null,
        client_key_path: tlsExtra.client_key_path ?? null,
      },
    };
  }

  function credentialsReady(): boolean {
    if (authMethod === 'password' || authMethod === 'ldap') {
      return (
        !!((authCredentials.username as string) ?? '').trim() &&
        !!(authCredentials.password as string)
      );
    }
    if (authMethod === 'wallet') {
      return authCredentials.walletFile instanceof File;
    }
    if (authMethod === 'kerberos') {
      return (
        authCredentials.keytabFile instanceof File &&
        !!((authCredentials.principal as string) ?? '').trim()
      );
    }
    if (authMethod === 'windows') return true;
    if (authMethod === 'azure_ad') {
      return !!((authCredentials.access_token as string) ?? '').trim();
    }
    if (authMethod === 'none') {
      return !!((authCredentials.hdfs_namenode as string) ?? '').trim();
    }
    return false;
  }

  const canTest = !!(
    name.trim() &&
    host.trim() &&
    database.trim() &&
    defaultSchema.trim() &&
    credentialsReady()
  );

  async function handleTest() {
    if (!canTest) return;
    setTesting(true);
    resetTest();
    try {
      const { credentials, tlsExtra } = await resolveUploads();
      const result = await triggerTest(buildPayload(credentials, tlsExtra));
      if (!result) return;
      setResolvedCredentials(credentials);
      setResolvedTlsExtra(tlsExtra);
      setTestResult({
        success: result.success,
        latency_ms: result.latency_ms,
        message: result.message,
      });
    } catch (err) {
      setTestResult({
        success: false,
        message:
          err instanceof Error ? err.message : 'Connection test failed',
      });
    } finally {
      setTesting(false);
    }
  }

  async function handleSave() {
    if (!testResult?.success) return;
    setSaving(true);
    try {
      const creds = resolvedCredentials ?? authCredentials;
      const tlsExtra = resolvedTlsExtra ?? {};
      await triggerCreate(buildPayload(creds, tlsExtra));
      onCreated();
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save connection');
    } finally {
      setSaving(false);
    }
  }

  const dbLabel =
    engineId === 'oracle'
      ? oracleConnType === 'sid'
        ? 'SID'
        : 'Service name'
      : 'Database';

  const infoBoxStyle: React.CSSProperties = {
    fontSize: 12,
    color: 'var(--text-faint)',
    background: 'var(--surface-2)',
    borderRadius: 6,
    padding: '8px 12px',
    border: '1px solid var(--border-soft)',
  };

  return (
    <div className="overlay" onClick={onClose}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 520 }}
      >
        <div className="modal-head">
          <DBLogo
            slug={db.id}
            size={38}
            radius={10}
            letter={db.letter}
            color={db.color}
          />
          <div>
            <h2>Connect {db.name}</h2>
          </div>
          <button className="icon-btn x" onClick={onClose}>
            <Icon name="x" />
          </button>
        </div>

        <div className="modal-body">
          <div className="grid" style={{ gap: 14 }}>

            {/* ── Connection fields ── */}
            <div className="field">
              <label>Connection name</label>
              <input
                className="input"
                placeholder={
                  engineId === 'oracle'
                    ? 'e.g. Core Banking'
                    : `e.g. ${db.name}`
                }
                value={name}
                onChange={(e) => { setName(e.target.value); resetTest(); }}
              />
            </div>

            <div className="row">
              <div className="field" style={{ flex: 2 }}>
                <label>{engineId === 'delta' ? 'Spark Master Host' : 'Host'}</label>
                <input
                  className="input mono"
                  placeholder={engineId === 'delta' ? '10.11.205.206' : 'db.bank.internal'}
                  value={host}
                  onChange={(e) => { setHost(e.target.value); resetTest(); }}
                />
              </div>
              <div className="field" style={{ flex: 1 }}>
                <label>{engineId === 'delta' ? 'Spark Master Port' : 'Port'}</label>
                <input
                  className="input mono"
                  value={port}
                  onChange={(e) => { setPort(e.target.value); resetTest(); }}
                />
              </div>
            </div>

            {engineId === 'oracle' && (
              <div className="field">
                <label>Connection type</label>
                <select
                  className="select"
                  value={oracleConnType}
                  onChange={(e) => {
                    setOracleConnType(
                      e.target.value as 'service_name' | 'sid',
                    );
                    resetTest();
                  }}
                >
                  <option value="service_name">Service name</option>
                  <option value="sid">SID</option>
                </select>
              </div>
            )}

            <div className="field">
              <label>
                {dbLabel}
                {engineId === 'delta' && (
                  <span
                    className="faint"
                    style={{ fontWeight: 400, marginLeft: 6, fontSize: 11.5 }}
                  >
                    (the Hive/Delta database inside spark_catalog — not the catalog itself; created automatically if it doesn't exist)
                  </span>
                )}
              </label>
              <input
                className="input mono"
                placeholder={
                  engineId === 'oracle' ? 'COREPDB' : engineId === 'delta' ? 'ekyc_db' : 'analytics'
                }
                value={database}
                onChange={(e) => {
                  const v = e.target.value;
                  setDatabase(v);
                  // Spark's database and schema are the same concept (unlike
                  // catalog, which is fixed as spark_catalog) — mirror the
                  // value so the object browser opens on the same namespace.
                  if (engineId === 'delta') setDefaultSchema(v);
                  resetTest();
                }}
              />
            </div>

            {engineId !== 'delta' && (
              <div className="field">
                <label>
                  Schema
                  <span
                    className="faint"
                    style={{ fontWeight: 400, marginLeft: 6, fontSize: 11.5 }}
                  >
                    (required — for the object browser)
                  </span>
                </label>
                <input
                  className="input mono"
                  placeholder={
                    engineId === 'oracle' ? 'ANALYTICS_OWNER' : 'public'
                  }
                  value={defaultSchema}
                  onChange={(e) => {
                    setDefaultSchema(e.target.value);
                    resetTest();
                  }}
                />
              </div>
            )}

            {/* ── Auth method selector ── */}
            {availableAuthMethods.length > 1 && (
              <div className="field">
                <label>Authentication method</label>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {availableAuthMethods.map((m) => (
                    <button
                      key={m.value}
                      type="button"
                      className={`btn btn-sm ${
                        authMethod === m.value ? 'btn-primary' : 'btn-ghost'
                      }`}
                      onClick={() => {
                        setAuthMethod(m.value);
                        setAuthCredentials({});
                        resetTest();
                      }}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* ── Password / LDAP ── */}
            {(authMethod === 'password' || authMethod === 'ldap') && (
              <>
                {authMethod === 'ldap' && (
                  <div style={infoBoxStyle}>
                    💡 Your credentials will be forwarded to the LDAP server
                    configured on the database.
                  </div>
                )}
                <div className="row">
                  <div className="field" style={{ flex: 1 }}>
                    <label>Username</label>
                    <input
                      className="input"
                      placeholder="readonly_svc"
                      value={(authCredentials.username as string) ?? ''}
                      onChange={(e) => {
                        setAuthCredentials((p) => ({
                          ...p,
                          username: e.target.value,
                        }));
                        resetTest();
                      }}
                    />
                  </div>
                  <div className="field" style={{ flex: 1 }}>
                    <label>Password</label>
                    <input
                      className="input"
                      type="password"
                      placeholder="••••••••••"
                      value={(authCredentials.password as string) ?? ''}
                      onChange={(e) => {
                        setAuthCredentials((p) => ({
                          ...p,
                          password: e.target.value,
                        }));
                        resetTest();
                      }}
                    />
                  </div>
                </div>
              </>
            )}

            {/* ── Oracle Wallet ── */}
            {authMethod === 'wallet' && (
              <>
                <div style={infoBoxStyle}>
                  💡 Upload your Oracle Wallet file (.sso or .p12). Credentials
                  are encrypted server-side and never stored in plaintext.
                </div>
                <div className="field">
                  <label>Oracle Wallet File</label>
                  <input
                    type="file"
                    accept=".sso,.p12"
                    title="Oracle Wallet file (.sso or .p12)"
                    className="input"
                    style={{ padding: '6px 10px', cursor: 'pointer' }}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) {
                        setAuthCredentials((p) => ({
                          ...p,
                          walletFile: file,
                          walletFileName: file.name,
                        }));
                        resetTest();
                      }
                    }}
                  />
                  {(authCredentials.walletFileName as string) && (
                    <div
                      className="faint mono"
                      style={{ fontSize: 11.5, marginTop: 4 }}
                    >
                      {authCredentials.walletFileName as string}
                    </div>
                  )}
                </div>
                <div className="field">
                  <label>
                    Wallet Password
                    <span
                      className="faint"
                      style={{ fontWeight: 400, marginLeft: 6, fontSize: 11.5 }}
                    >
                      (optional)
                    </span>
                  </label>
                  <input
                    className="input"
                    type="password"
                    placeholder="Leave empty if no wallet password"
                    value={(authCredentials.walletPassword as string) ?? ''}
                    onChange={(e) => {
                      setAuthCredentials((p) => ({
                        ...p,
                        walletPassword: e.target.value,
                      }));
                      resetTest();
                    }}
                  />
                </div>
              </>
            )}

            {/* ── Kerberos ── */}
            {authMethod === 'kerberos' && (
              <>
                <div style={infoBoxStyle}>
                  💡 Requires a keytab file and principal. The backend must run
                  with Oracle Instant Client in Thick Mode.
                </div>
                <div className="field">
                  <label>Kerberos Keytab File</label>
                  <input
                    type="file"
                    title="Kerberos keytab file"
                    className="input"
                    style={{ padding: '6px 10px', cursor: 'pointer' }}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) {
                        setAuthCredentials((p) => ({
                          ...p,
                          keytabFile: file,
                          keytabFileName: file.name,
                        }));
                        resetTest();
                      }
                    }}
                  />
                  {(authCredentials.keytabFileName as string) && (
                    <div
                      className="faint mono"
                      style={{ fontSize: 11.5, marginTop: 4 }}
                    >
                      {authCredentials.keytabFileName as string}
                    </div>
                  )}
                </div>
                <div className="field">
                  <label>Principal</label>
                  <input
                    className="input mono"
                    placeholder="user@REALM.COM"
                    value={(authCredentials.principal as string) ?? ''}
                    onChange={(e) => {
                      setAuthCredentials((p) => ({
                        ...p,
                        principal: e.target.value,
                      }));
                      resetTest();
                    }}
                  />
                </div>
              </>
            )}

            {/* ── Windows Auth (NTLM) ── */}
            {authMethod === 'windows' && (
              <>
                <div style={infoBoxStyle}>
                  💡 Uses NTLM system credentials. Only works when the InsightX
                  backend runs on a Windows host in the same Active Directory
                  domain as SQL Server.
                </div>
                <div className="field">
                  <label>
                    Domain
                    <span
                      className="faint"
                      style={{ fontWeight: 400, marginLeft: 6, fontSize: 11.5 }}
                    >
                      (optional)
                    </span>
                  </label>
                  <input
                    className="input mono"
                    placeholder="CORP"
                    value={(authCredentials.domain as string) ?? ''}
                    onChange={(e) => {
                      setAuthCredentials((p) => ({
                        ...p,
                        domain: e.target.value,
                      }));
                      resetTest();
                    }}
                  />
                </div>
              </>
            )}

            {/* ── Azure AD ── */}
            {authMethod === 'azure_ad' && (
              <>
                <div style={infoBoxStyle}>
                  💡 Obtain an Azure AD access token and paste it below.
                  Browser-based OAuth2 login will be available in a future
                  release.
                </div>
                <div className="field">
                  <label>Access Token</label>
                  <textarea
                    className="input mono"
                    rows={4}
                    placeholder="Paste your Azure AD access token here"
                    style={{ resize: 'none', fontSize: 12 }}
                    value={(authCredentials.access_token as string) ?? ''}
                    onChange={(e) => {
                      setAuthCredentials((p) => ({
                        ...p,
                        access_token: e.target.value,
                      }));
                      resetTest();
                    }}
                  />
                </div>
              </>
            )}

            {/* ── Delta Lakehouse (Spark) — HDFS namenode ── */}
            {authMethod === 'none' && (
              <>
                <div style={infoBoxStyle}>
                  💡 Open Spark standalone cluster — no username or password
                  required. Java/Spark install paths are configured once on the
                  backend server, not per-connection. Testing the connection
                  will create the database above under spark_catalog if it
                  doesn't already exist.
                </div>
                <div className="field">
                  <label>HDFS Namenode</label>
                  <input
                    className="input mono"
                    placeholder="hdfs://10.11.204.203:9000"
                    value={(authCredentials.hdfs_namenode as string) ?? ''}
                    onChange={(e) => {
                      setAuthCredentials((p) => ({
                        ...p,
                        hdfs_namenode: e.target.value,
                      }));
                      resetTest();
                    }}
                  />
                </div>
                <div className="field">
                  <label>
                    Warehouse Directory
                    <span
                      className="faint"
                      style={{ fontWeight: 400, marginLeft: 6, fontSize: 11.5 }}
                    >
                      (optional — defaults to &lt;namenode&gt;/user/spark/warehouse)
                    </span>
                  </label>
                  <input
                    className="input mono"
                    placeholder="hdfs://10.11.204.203:9000/user/spark/warehouse"
                    value={(authCredentials.warehouse_dir as string) ?? ''}
                    onChange={(e) => {
                      setAuthCredentials((p) => ({
                        ...p,
                        warehouse_dir: e.target.value,
                      }));
                      resetTest();
                    }}
                  />
                </div>
              </>
            )}

            {/* ── TLS toggle (not applicable to Delta) ── */}
            {engineId !== 'delta' && (
              <label
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 9,
                  cursor: 'pointer',
                  fontSize: 13,
                  color: 'var(--text-muted)',
                  fontWeight: 500,
                }}
              >
                <input
                  type="checkbox"
                  checked={useTls}
                  onChange={(e) => { setUseTls(e.target.checked); resetTest(); }}
                />
                Use SSL / TLS encrypted connection
              </label>
            )}

            {/* ── TLS options ── */}
            {engineId !== 'delta' && useTls && (
              <>
                {tlsModes.length > 1 && (
                  <div className="field">
                    <label>SSL mode</label>
                    <select
                      className="select"
                      title="SSL / TLS mode"
                      value={tlsMode}
                      onChange={(e) => {
                        setTlsMode(e.target.value);
                        resetTest();
                      }}
                    >
                      {tlsModes.map((m) => (
                        <option key={m.value} value={m.value}>
                          {m.label}
                        </option>
                      ))}
                    </select>
                  </div>
                )}

                <label
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 9,
                    cursor: 'pointer',
                    fontSize: 13,
                    color: 'var(--text-muted)',
                    fontWeight: 500,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={tlsVerify}
                    onChange={(e) => {
                      setTlsVerify(e.target.checked);
                      resetTest();
                    }}
                  />
                  Verify server certificate
                </label>

                {!tlsVerify && (
                  <div
                    style={{
                      fontSize: 12,
                      color: 'oklch(0.65 0.15 55)',
                      background: 'oklch(0.2 0.04 55)',
                      border: '1px solid oklch(0.35 0.08 55)',
                      borderRadius: 6,
                      padding: '8px 12px',
                    }}
                  >
                    ⚠ Skipping cert verification exposes this connection to
                    man-in-the-middle attacks. Only use for testing with
                    self-signed certificates.
                  </div>
                )}

                {tlsVerify && (
                  <div className="field">
                    <label>
                      CA Certificate
                      <span
                        className="faint"
                        style={{
                          fontWeight: 400,
                          marginLeft: 6,
                          fontSize: 11.5,
                        }}
                      >
                        (optional)
                      </span>
                    </label>
                    <input
                      type="file"
                      accept=".pem,.crt,.cer"
                      title="CA certificate file (.pem or .crt)"
                      className="input"
                      style={{ padding: '6px 10px', cursor: 'pointer' }}
                      onChange={(e) => {
                        setCaCertFile(e.target.files?.[0] ?? null);
                        resetTest();
                      }}
                    />
                    {caCertFile && (
                      <div
                        className="faint mono"
                        style={{ fontSize: 11.5, marginTop: 4 }}
                      >
                        {caCertFile.name}
                      </div>
                    )}
                  </div>
                )}

                <div className="field">
                  <label>
                    Client Certificate
                    <span
                      className="faint"
                      style={{ fontWeight: 400, marginLeft: 6, fontSize: 11.5 }}
                    >
                      (optional — for mTLS)
                    </span>
                  </label>
                  <input
                    type="file"
                    accept=".pem,.crt,.cer"
                    title="Client certificate file (.pem or .crt)"
                    className="input"
                    style={{ padding: '6px 10px', cursor: 'pointer' }}
                    onChange={(e) => {
                      setClientCertFile(e.target.files?.[0] ?? null);
                      resetTest();
                    }}
                  />
                  {clientCertFile && (
                    <div
                      className="faint mono"
                      style={{ fontSize: 11.5, marginTop: 4 }}
                    >
                      {clientCertFile.name}
                    </div>
                  )}
                </div>

                <div className="field">
                  <label>
                    Client Private Key
                    <span
                      className="faint"
                      style={{ fontWeight: 400, marginLeft: 6, fontSize: 11.5 }}
                    >
                      (optional — for mTLS)
                    </span>
                  </label>
                  <input
                    type="file"
                    accept=".pem,.key"
                    title="Client private key file (.pem or .key)"
                    className="input"
                    style={{ padding: '6px 10px', cursor: 'pointer' }}
                    onChange={(e) => {
                      setClientKeyFile(e.target.files?.[0] ?? null);
                      resetTest();
                    }}
                  />
                  {clientKeyFile && (
                    <div
                      className="faint mono"
                      style={{ fontSize: 11.5, marginTop: 4 }}
                    >
                      {clientKeyFile.name}
                    </div>
                  )}
                </div>
              </>
            )}

            {testResult?.success && (
              <div
                className="pill pill-green"
                style={{ alignSelf: 'flex-start', padding: '6px 12px' }}
              >
                <Icon name="check" size={14} />
                Connection successful
                {testResult.latency_ms != null
                  ? ` · ${testResult.latency_ms}ms`
                  : ''}
              </div>
            )}
            {testResult && !testResult.success && (
              <div
                className="pill pill-red"
                style={{ alignSelf: 'flex-start', padding: '6px 12px' }}
              >
                ⚠ {testResult.message || 'Connection failed'}
              </div>
            )}
          </div>
        </div>

        <div className="modal-foot">
          <button
            className="btn btn-ghost"
            onClick={handleTest}
            disabled={testing || !canTest}
          >
            <Icon name="plug" size={14} />
            {testing ? 'Testing…' : 'Test connection'}
          </button>
          <button
            className="btn btn-primary"
            onClick={handleSave}
            disabled={!testResult?.success || saving}
          >
            <Icon name="check" size={14} />
            {saving ? 'Connecting…' : 'Connect'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─── Table Card ─── */

function formatRowCount(count: number): string {
  if (count <= 0) return '—';
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(2)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}K`;
  return count.toLocaleString();
}

function TableCard({ obj, onClick }: { obj: SchemaObject; onClick?: () => void }) {
  const isView = obj.type === 'VIEW';
  const iconBg = isView ? 'oklch(0.92 0.04 280)' : 'oklch(0.91 0.05 240)';
  const iconColor = isView ? 'oklch(0.45 0.18 280)' : 'oklch(0.48 0.18 240)';

  return (
    <div
      onClick={onClick}
      style={{
        padding: '18px 20px',
        borderRadius: 14,
        border: '1px solid var(--border-soft)',
        backgroundColor: 'var(--surface-1, var(--surface-2))',
        display: 'flex',
        alignItems: 'center',
        gap: 16,
        cursor: onClick ? 'pointer' : undefined,
        transition: 'border-color 0.15s, box-shadow 0.15s',
      }}
      onMouseEnter={(e) => {
        if (!onClick) return;
        const el = e.currentTarget as HTMLDivElement;
        el.style.borderColor = 'var(--accent)';
        el.style.boxShadow = '0 0 0 2px var(--accent-ring)';
      }}
      onMouseLeave={(e) => {
        if (!onClick) return;
        const el = e.currentTarget as HTMLDivElement;
        el.style.borderColor = 'var(--border-soft)';
        el.style.boxShadow = 'none';
      }}
    >
      {/* Icon */}
      <div
        style={{
          width: 44,
          height: 44,
          borderRadius: 10,
          background: iconBg,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
        }}
      >
        <Icon name="table" size={20} style={{ color: iconColor }} />
      </div>

      {/* Name + meta */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontWeight: 700,
            fontSize: 13,
            letterSpacing: '0.05em',
            color: 'var(--text)',
            marginBottom: 6,
            wordBreak: 'break-word',
          }}
        >
          {obj.name}
        </div>
        <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>
          {formatRowCount(obj.row_count)} rows
          <span style={{ margin: '0 5px', opacity: 0.35 }}>·</span>
          {obj.column_count} cols
        </div>
      </div>

      {/* Type badge */}
      {isView && (
        <span
          className="pill"
          style={{
            fontSize: 10.5,
            flexShrink: 0,
            background: 'oklch(0.88 0.06 280)',
            color: 'oklch(0.42 0.18 280)',
            border: 'none',
          }}
        >
          VIEW
        </span>
      )}
    </div>
  );
}

/* ─── Table Browser — Card Grid with Pagination & Search ─── */

function TableBrowserView({
  source,
  onBack,
  onRetested,
  onTableSelected,
}: {
  source: DatasourceRecord;
  onBack: () => void;
  onRetested: (updated: DatasourceRecord) => void;
  onTableSelected: (obj: SchemaObject) => void;
}) {
  const meta =
    ENGINE_DISPLAY[source.engine] ??
    { slug: 'oracle', letter: '?', color: '#888', label: source.engine };
  const schema = source.default_schema;

  const [currentPage, setCurrentPage] = useState(1);
  const [allObjects, setAllObjects] = useState<SchemaObject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retesting, setRetesting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SchemaObject[] | null>(null);
  const [isSearching, setIsSearching] = useState(false);

  // API mutations — imperative triggers (useSWRMutation) over the common fetcher.
  const { trigger: triggerBrowse } = useSWRMutation(
    'datasource-browse',
    (_key: string, { arg }: { arg: { offset: number } }) =>
      get<TableBrowseResult>(getDatasourceTables(source.id, schema, arg.offset, 100)),
  );
  const { trigger: triggerSearch } = useSWRMutation(
    'datasource-search',
    (_key: string, { arg }: { arg: string }) =>
      get<SearchTableResult>(getDatasourceSearch(source.id, schema, arg)),
  );
  const { trigger: triggerRetest } = useSWRMutation(
    'datasource-retest',
    (_key: string, { arg }: { arg: string }) =>
      post<TestConnectionResult>(postDatasourceRetest(arg)),
  );

  const PAGE_SIZE = 10;
  const displayObjects = searchResults ?? allObjects;
  const totalPages = Math.ceil(displayObjects.length / PAGE_SIZE);
  const startIdx = (currentPage - 1) * PAGE_SIZE;
  const endIdx = startIdx + PAGE_SIZE;
  const pageObjects = displayObjects.slice(startIdx, endIdx);

  async function loadAllTables() {
    setLoading(true);
    setError(null);
    setSearchResults(null);
    setCurrentPage(1);

    try {
      let allData: SchemaObject[] = [];
      let offset = 0;
      let hasMore = true;

      while (hasMore) {
        const data = await triggerBrowse({ offset });
        if (!data) break;
        allData = allData.concat(data.objects);
        hasMore = data.has_more;
        offset += 100;
      }

      setAllObjects(allData);

      // Fire-and-forget FK discovery — runs in the background after tables load.
      // The 202 response means the server accepted it; we don't await the result.
      post(postSyncRelationships(source.id, schema)).catch(() => {/* silent */});
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load tables';
      toast.error(msg);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  async function handleSearch(query: string) {
    setSearchQuery(query);
    if (!query.trim()) {
      setSearchResults(null);
      setCurrentPage(1);
      return;
    }

    setIsSearching(true);
    try {
      const result = await triggerSearch(query.trim());
      setSearchResults(result?.objects ?? []);
      setCurrentPage(1);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Search failed');
    } finally {
      setIsSearching(false);
    }
  }

  useEffect(() => {
    loadAllTables();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.id]);

  async function handleRetest() {
    setRetesting(true);
    try {
      await triggerRetest(source.id);
      onRetested({
        ...source,
        last_tested_at: new Date().toISOString(),
        last_test_status: 'success',
      });
    } catch {
      onRetested({
        ...source,
        last_tested_at: new Date().toISOString(),
        last_test_status: 'failed',
      });
    } finally {
      setRetesting(false);
    }
  }

  async function handleSync() {
    setSyncing(true);
    try {
      await loadAllTables();
    } finally {
      setSyncing(false);
    }
  }

  const statusClass =
    source.last_test_status === 'success'
      ? 'pill-green dot'
      : source.last_test_status === 'failed'
        ? 'pill-red dot'
        : '';
  const statusLabel =
    source.last_test_status === 'success'
      ? 'Connected'
      : source.last_test_status === 'failed'
        ? 'Failed'
        : 'Not tested';

  const totalObjects = allObjects.length;

  return (
    <div className="page-inner fade-up">
      <button className="back-link" onClick={onBack}>
        <Icon name="chevronL" /> Data sources
      </button>

      <div
        className="between"
        style={{ margin: '6px 0 22px' }}
      >
        <div
          className="row"
          style={{ alignItems: 'center', gap: 14 }}
        >
          <DBLogo
            slug={meta.slug}
            size={44}
            radius={12}
            letter={meta.letter}
            color={meta.color}
          />
          <div>
            <h1 className="section-title">{source.name}</h1>
            <div
              className="faint mono"
              style={{ fontSize: 12.5, marginTop: 3 }}
            >
              {source.host}:{source.port}/{source.database_name}
            </div>
          </div>
        </div>

        <div
          className="row"
          style={{ alignItems: 'center', gap: 8 }}
        >
          <span className={`pill ${statusClass}`}>{statusLabel}</span>
          <button
            className="btn btn-ghost btn-sm"
            onClick={handleRetest}
            disabled={retesting}
          >
            <Icon name="refresh" size={14} />
            {retesting ? 'Testing…' : 'Re-test'}
          </button>
          <button
            className="btn btn-ghost btn-sm"
            onClick={handleSync}
            disabled={syncing || loading}
          >
            <Icon name="refresh" size={14} />
            {syncing ? 'Syncing…' : 'Sync now'}
          </button>
        </div>
      </div>

      {/* Search bar */}
      <div className="card card-pad" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <Icon name="search" size={16} style={{ color: 'var(--text-faint)' }} />
          <input
            className="input"
            placeholder={`Search tables in ${schema}…`}
            value={searchQuery}
            onChange={(e) => handleSearch(e.target.value)}
            style={{ flex: 1, padding: '8px 12px' }}
          />
          {searchQuery && (
            <button
              type="button"
              title="Clear search"
              onClick={() => handleSearch('')}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: 'var(--text-faint)',
              }}
            >
              <Icon name="x" size={16} />
            </button>
          )}
        </div>
      </div>

      {/* Loading state */}
      {loading && (
        <div
          className="card card-pad"
          style={{ textAlign: 'center', color: 'var(--text-faint)' }}
        >
          Loading tables from <span className="mono">{schema}</span>…
        </div>
      )}

      {/* Error state */}
      {error && !loading && (
        <div
          className="card card-pad"
          style={{
            color: 'var(--danger)',
            display: 'flex',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <span>⚠ {error}</span>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => loadAllTables()}
            style={{ marginLeft: 'auto' }}
          >
            Retry
          </button>
        </div>
      )}

      {/* Empty state */}
      {!loading &&
        !error &&
        (searchResults !== null
          ? searchResults.length === 0
          : totalObjects === 0) && (
          <div className="empty-state">
            <div className="es-ic">
              <Icon name="table" size={22} />
            </div>
            <h3>
              {searchResults !== null
                ? 'No matching tables'
                : `No objects in ${schema}`}
            </h3>
            <p>
              {searchResults !== null
                ? 'Try a different search term'
                : 'The schema may be empty or the credentials may have limited permissions.'}
            </p>
          </div>
        )}

      {/* Results grid */}
      {!loading &&
        !error &&
        displayObjects.length > 0 && (
          <>
            {/* Summary + filter row */}
            <div
              className="between"
              style={{ marginBottom: 16 }}
            >
              <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>
                {searchResults !== null
                  ? `${displayObjects.length} result${displayObjects.length !== 1 ? 's' : ''}`
                  : `${totalObjects} table${totalObjects !== 1 ? 's' : ''}`}
              </span>
              {searchResults === null && (
                <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>
                  {`Showing ${startIdx + 1}–${Math.min(endIdx, totalObjects)}`}
                </span>
              )}
            </div>

            {/* Card grid */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(2, 1fr)',
                gap: '14px',
                marginBottom: 24,
              }}
            >
              {pageObjects.map((obj) => (
                <TableCard
                  key={`${obj.type}-${obj.name}`}
                  obj={obj}
                  onClick={() => onTableSelected(obj)}
                />
              ))}
            </div>

            {/* Pagination controls */}
            {!searchResults && totalPages > 1 && (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '8px',
                  padding: '16px',
                  borderTop: '1px solid var(--border-soft)',
                }}
              >
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => setCurrentPage(1)}
                  disabled={currentPage === 1}
                >
                  ⟨⟨
                </button>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() =>
                    setCurrentPage((p) => Math.max(1, p - 1))
                  }
                  disabled={currentPage === 1}
                >
                  ⟨
                </button>

                {/* Page numbers */}
                <div
                  style={{
                    display: 'flex',
                    gap: '4px',
                    alignItems: 'center',
                  }}
                >
                  {Array.from(
                    {
                      length: Math.min(
                        5,
                        totalPages,
                      ),
                    },
                    (_, i) => {
                      const startPage = Math.max(
                        1,
                        currentPage -
                          Math.floor(5 / 2),
                      );
                      const page =
                        startPage + i;
                      if (page > totalPages)
                        return null;
                      return (
                        <button
                          key={page}
                          className={`btn btn-sm ${currentPage === page
                            ? 'btn-primary'
                            : 'btn-ghost'
                            }`}
                          onClick={() =>
                            setCurrentPage(
                              page,
                            )
                          }
                          style={{
                            minWidth: '32px',
                          }}
                        >
                          {page}
                        </button>
                      );
                    },
                  )}
                </div>

                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() =>
                    setCurrentPage((p) =>
                      Math.min(
                        totalPages,
                        p + 1,
                      ),
                    )
                  }
                  disabled={currentPage === totalPages}
                >
                  ⟩
                </button>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => setCurrentPage(totalPages)}
                  disabled={currentPage === totalPages}
                >
                  ⟩⟩
                </button>
              </div>
            )}
          </>
        )}
    </div>
  );
}

/* ─── Table Annotation View ─── */

function TableAnnotationView({
  source,
  table,
  schema,
  onBack,
}: {
  source: DatasourceRecord;
  table: SchemaObject;
  schema: string;
  onBack: () => void;
}) {
  const [activeTab, setActiveTab] = useState<'columns' | 'relationships'>('columns');
  const [columnMeta, setColumnMeta] = useState<ColumnMeta[]>([]);
  const [annotationDrafts, setAnnotationDrafts] = useState<Record<string, string>>({});
  const [tableDesc, setTableDesc] = useState('');
  const [metaLoading, setMetaLoading] = useState(true);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedOk, setSavedOk] = useState(false);
  const [relationships, setRelationships] = useState<Relationship[]>([]);
  const [relsLoading, setRelsLoading] = useState(true);
  const [addingRel, setAddingRel] = useState(false);
  const [relSaving, setRelSaving] = useState(false);
  const [newRel, setNewRel] = useState<RelationshipCreatePayload>({
    from_table: table.name,
    from_column: '',
    to_table: '',
    to_column: '',
    relationship_type: 'many-to-one',
  });

  useEffect(() => {
    setMetaLoading(true);
    setMetaError(null);
    setRelsLoading(true);

    Promise.all([
      get<ColumnMetaListResult>(getDatasourceColumns(source.id, schema, table.name)),
      get<TableAnnotationResult>(getTableAnnotations(source.id, schema, table.name)),
      get<RelationshipListResult>(getSchemaRelationships(source.id, schema)),
    ])
      .then(([metaRes, annotRes, relRes]) => {
        setColumnMeta(metaRes.columns);
        setTableDesc(annotRes.description ?? '');
        const drafts: Record<string, string> = {};
        annotRes.column_annotations.forEach((a) => {
          drafts[a.column_name] = a.annotation ?? '';
        });
        setAnnotationDrafts(drafts);
        setRelationships(relRes.relationships);
      })
      .catch((err) => {
        setMetaError(err instanceof Error ? err.message : 'Failed to load table data');
      })
      .finally(() => {
        setMetaLoading(false);
        setRelsLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.id, schema, table.name]);

  const tableRelationships = relationships.filter(
    (r) => r.from_table === table.name || r.to_table === table.name,
  );

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    setSavedOk(false);
    try {
      const payload: TableAnnotationPutPayload = {
        description: tableDesc.trim() || null,
        annotations: columnMeta.map((col) => ({
          column_name: col.name,
          annotation: annotationDrafts[col.name]?.trim() || null,
        })),
      };
      await put<TableAnnotationResult, TableAnnotationPutPayload>(
        putTableAnnotations(source.id, schema, table.name),
        payload,
      );
      setSavedOk(true);
      setTimeout(() => setSavedOk(false), 2500);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  async function handleAddRelationship() {
    if (!newRel.from_column.trim() || !newRel.to_table.trim() || !newRel.to_column.trim()) return;
    setRelSaving(true);
    try {
      const created = await post<Relationship, RelationshipCreatePayload>(
        postSchemaRelationship(source.id, schema),
        newRel,
      );
      setRelationships((prev) => [...prev, created]);
      setAddingRel(false);
      setNewRel({ from_table: table.name, from_column: '', to_table: '', to_column: '', relationship_type: 'many-to-one' });
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Failed to add relationship');
    } finally {
      setRelSaving(false);
    }
  }

  async function handleDeleteRelationship(id: string) {
    try {
      await del(deleteSchemaRelationship(source.id, schema, id));
      setRelationships((prev) => prev.filter((r) => r.id !== id));
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Failed to delete relationship');
    }
  }

  const rowsLabel = table.row_count > 0 ? table.row_count.toLocaleString() : '—';

  return (
    <div className="page-inner fade-up">
      {/* Back link */}
      <button className="back-link" onClick={onBack}>
        <Icon name="chevronL" /> {source.name}
      </button>

      {/* Header */}
      <div className="between" style={{ margin: '6px 0 22px', alignItems: 'flex-start' }}>
        <div className="row" style={{ alignItems: 'center', gap: 12 }}>
          <div>
            <h1 className="section-title mono" style={{ marginBottom: 2 }}>{table.name}</h1>
            <span className="faint" style={{ fontSize: 12.5 }}>
              {rowsLabel} rows · <span className="mono">{schema}</span>
            </span>
          </div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          {savedOk && (
            <span className="pill pill-green" style={{ padding: '6px 12px' }}>
              <Icon name="check" size={13} /> Saved
            </span>
          )}
          {saveError && (
            <span className="pill pill-red" style={{ padding: '6px 12px', maxWidth: 260 }}>
              ⚠ {saveError}
            </span>
          )}
          <button
            className="btn btn-primary"
            onClick={handleSave}
            disabled={saving || metaLoading}
          >
            <Icon name="check" size={14} />
            {saving ? 'Saving…' : 'Save annotations'}
          </button>
        </div>
      </div>

      {/* Table description */}
      <div className="card card-pad" style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10 }}>
          Table description
        </div>
        <textarea
          className="input"
          rows={3}
          placeholder="Describe this table in plain language. This helps the AI understand the table's purpose…"
          value={tableDesc}
          onChange={(e) => setTableDesc(e.target.value)}
          style={{ width: '100%', resize: 'vertical', fontSize: 13 }}
        />
      </div>

      {/* Tabs */}
      <div className="tabs">
        <button
          className={`tab${activeTab === 'columns' ? ' active' : ''}`}
          onClick={() => setActiveTab('columns')}
        >
          Columns{columnMeta.length ? ` (${columnMeta.length})` : ''}
        </button>
        <button
          className={`tab${activeTab === 'relationships' ? ' active' : ''}`}
          onClick={() => setActiveTab('relationships')}
        >
          Relationships ({tableRelationships.length})
        </button>
      </div>

      {/* ── Columns tab ── */}
      {activeTab === 'columns' && (
        <>
          {metaLoading && (
            <div className="card card-pad" style={{ color: 'var(--text-faint)', textAlign: 'center' }}>
              Loading columns…
            </div>
          )}
          {metaError && !metaLoading && (
            <div className="card card-pad" style={{ color: 'var(--danger)' }}>⚠ {metaError}</div>
          )}
          {!metaLoading && !metaError && columnMeta.length === 0 && (
            <div className="empty-state">
              <div className="es-ic"><Icon name="table" size={22} /></div>
              <h3>No columns found</h3>
              <p>The table may be empty or the credentials may have limited permissions.</p>
            </div>
          )}
          {!metaLoading && !metaError && columnMeta.length > 0 && (
            <div className="card" style={{ overflow: 'hidden' }}>
              <table className="annot-table">
                <thead>
                  <tr>
                    <th style={{ width: '28%' }}>Column</th>
                    <th style={{ width: '18%' }}>Type</th>
                    <th>Annotation</th>
                    <th style={{ width: 80, textAlign: 'center' }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {columnMeta.map((col) => {
                    const val = annotationDrafts[col.name] ?? '';
                    return (
                      <tr key={col.name}>
                        <td>
                          <div className="row" style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                            <span className="mono" style={{ fontSize: 13, fontWeight: 600 }}>{col.name}</span>
                            {col.is_primary_key && <span className="col-key pk">PK</span>}
                            {col.is_foreign_key && <span className="col-key fk">FK</span>}
                          </div>
                        </td>
                        <td className="mono" style={{ color: 'var(--text-faint)', fontSize: 12 }}>{col.type}</td>
                        <td>
                          <input
                            className="annot-input"
                            value={val}
                            placeholder="Describe this column…"
                            onChange={(e) =>
                              setAnnotationDrafts((p) => ({ ...p, [col.name]: e.target.value }))
                            }
                          />
                        </td>
                        <td style={{ textAlign: 'center' }}>
                          {val.trim() ? (
                            <Icon name="check" size={15} style={{ color: 'var(--success)' }} />
                          ) : (
                            <span className="pill pill-amber" style={{ fontSize: 10.5 }}>Empty</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {/* ── Relationships tab ── */}
      {activeTab === 'relationships' && (
        <div className="card card-pad">
          <div className="between" style={{ marginBottom: 16 }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>
              Relationships involving <span className="mono">{table.name}</span>
            </span>
            <button className="btn btn-ghost btn-sm" onClick={() => setAddingRel(true)}>
              + Add
            </button>
          </div>

          {relsLoading && (
            <div style={{ color: 'var(--text-faint)', fontSize: 13 }}>Loading…</div>
          )}

          {!relsLoading && tableRelationships.length === 0 && !addingRel && (
            <div style={{ color: 'var(--text-faint)', fontSize: 13 }}>
              No relationships defined for this table yet.
            </div>
          )}

          {tableRelationships.map((r) => (
            <div key={r.id} className="rel-row">
              <span className="mono" style={{ flex: 1 }}>
                {r.from_table}<span style={{ color: 'var(--text-faint)' }}>.</span>{r.from_column}
                <span className="rel-arrow"> → </span>
                {r.to_table}<span style={{ color: 'var(--text-faint)' }}>.</span>{r.to_column}
              </span>
              <span className="pill">{r.relationship_type}</span>
              {r.is_discovered ? (
                <span className="pill pill-blue" style={{ fontSize: 10.5 }} title="Discovered automatically from FK constraints">
                  auto
                </span>
              ) : (
                <button
                  className="icon-btn"
                  title="Delete relationship"
                  onClick={() => handleDeleteRelationship(r.id)}
                  style={{ color: 'var(--text-faint)', padding: '3px 6px', borderRadius: 5, border: '1px solid transparent', background: 'none', cursor: 'pointer' }}
                  onMouseEnter={(e) => { const b = e.currentTarget as HTMLButtonElement; b.style.color = 'var(--danger)'; b.style.borderColor = 'var(--danger)'; }}
                  onMouseLeave={(e) => { const b = e.currentTarget as HTMLButtonElement; b.style.color = 'var(--text-faint)'; b.style.borderColor = 'transparent'; }}
                >
                  <Icon name="x" size={13} />
                </button>
              )}
            </div>
          ))}

          {addingRel && (
            <div style={{ borderTop: tableRelationships.length > 0 ? '1px solid var(--border-soft)' : 'none', paddingTop: tableRelationships.length > 0 ? 16 : 0, marginTop: tableRelationships.length > 0 ? 4 : 0 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-faint)', marginBottom: 12, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                New relationship
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', gap: 10, alignItems: 'end', marginBottom: 12 }}>
                <div className="field" style={{ margin: 0 }}>
                  <label style={{ fontSize: 11.5 }}>From table · column</label>
                  <div className="row" style={{ gap: 6 }}>
                    <input
                      className="input mono"
                      style={{ flex: 1, fontSize: 12 }}
                      placeholder="table"
                      value={newRel.from_table}
                      onChange={(e) => setNewRel((p) => ({ ...p, from_table: e.target.value }))}
                    />
                    <input
                      className="input mono"
                      style={{ flex: 1, fontSize: 12 }}
                      placeholder="column"
                      value={newRel.from_column}
                      onChange={(e) => setNewRel((p) => ({ ...p, from_column: e.target.value }))}
                    />
                  </div>
                </div>
                <div style={{ color: 'var(--text-faint)', fontSize: 18, paddingBottom: 6 }}>→</div>
                <div className="field" style={{ margin: 0 }}>
                  <label style={{ fontSize: 11.5 }}>To table · column</label>
                  <div className="row" style={{ gap: 6 }}>
                    <input
                      className="input mono"
                      style={{ flex: 1, fontSize: 12 }}
                      placeholder="table"
                      value={newRel.to_table}
                      onChange={(e) => setNewRel((p) => ({ ...p, to_table: e.target.value }))}
                    />
                    <input
                      className="input mono"
                      style={{ flex: 1, fontSize: 12 }}
                      placeholder="column"
                      value={newRel.to_column}
                      onChange={(e) => setNewRel((p) => ({ ...p, to_column: e.target.value }))}
                    />
                  </div>
                </div>
              </div>
              <div className="between">
                <div className="field" style={{ margin: 0 }}>
                  <label style={{ fontSize: 11.5 }}>Relationship type</label>
                  <select
                    className="select"
                    value={newRel.relationship_type}
                    onChange={(e) => setNewRel((p) => ({ ...p, relationship_type: e.target.value as RelationshipType }))}
                  >
                    <option value="many-to-one">Many-to-one</option>
                    <option value="one-to-one">One-to-one</option>
                    <option value="many-to-many">Many-to-many</option>
                  </select>
                </div>
                <div className="row" style={{ gap: 8, alignSelf: 'flex-end' }}>
                  <button className="btn btn-ghost btn-sm" onClick={() => setAddingRel(false)}>
                    Cancel
                  </button>
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={handleAddRelationship}
                    disabled={relSaving || !newRel.from_column.trim() || !newRel.to_table.trim() || !newRel.to_column.trim()}
                  >
                    {relSaving ? 'Saving…' : 'Add'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ─── Main Data Source page ─── */

export default function DataSourcePage() {
  const [credDb, setCredDb] = useState<DbType | null>(null);
  const [selectedSource, setSelectedSource] =
    useState<DatasourceRecord | null>(null);
  const [selectedTable, setSelectedTable] = useState<SchemaObject | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<DatasourceRecord | null>(null);
  const [deactivatingId, setDeactivatingId] = useState<string | null>(null);
  const [retestingId, setRetestingId] = useState<string | null>(null);

  // List (GET) — declarative read via useSWR using the common `get` fetcher.
  // The endpoint wraps rows in { data, count }; unwrap to the array.
  const {
    data: datasources = [],
    error: listFetchError,
    isLoading: loadingList,
    mutate: mutateDatasources,
  } = useSWR<DatasourceRecord[]>(getDatasources, (url: string) =>
    get<DatasourceListResponse>(url).then((r) => r.data),
  );
  
  useEffect(() => {
    if (listFetchError) {
      toast.error('Could not load data sources. Is the backend running?', {
        toastId: 'datasource-list-error',
      });
    }
  }, [listFetchError]);

  const { trigger: triggerDelete } = useSWRMutation(
    'datasource-delete',
    (_key: string, { arg }: { arg: string }) => del(deleteDatasourceUrl(arg)),
  );

  const { trigger: triggerDeactivate } = useSWRMutation(
    'datasource-deactivate',
    (_key: string, { arg }: { arg: string }) =>
      patch<DatasourceRecord>(patchDatasourceDeactivate(arg)),
  );

  const { trigger: triggerRetestList } = useSWRMutation(
    'datasource-retest-list',
    (_key: string, { arg }: { arg: string }) =>
      post<{ success: boolean }>(postDatasourceRetest(arg)),
  );

  function handleRetested(updated: DatasourceRecord) {
    // Optimistic cache update (no refetch) — matches the previous local update.
    mutateDatasources(
      (prev) => (prev ?? []).map((s) => (s.id === updated.id ? updated : s)),
      { revalidate: false },
    );
    if (selectedSource?.id === updated.id) setSelectedSource(updated);
  }

  async function handleDeactivate(e: React.MouseEvent, source: DatasourceRecord) {
    e.stopPropagation();
    setDeactivatingId(source.id);
    try {
      await triggerDeactivate(source.id);
      mutateDatasources(
        (prev) => (prev ?? []).map((s) =>
          s.id === source.id ? { ...s, is_active: false } : s,
        ),
        { revalidate: false },
      );
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to deactivate connection');
    } finally {
      setDeactivatingId(null);
    }
  }

  async function handleRetestFromList(e: React.MouseEvent, source: DatasourceRecord) {
    e.stopPropagation();
    setRetestingId(source.id);
    try {
      await triggerRetestList(source.id);
      mutateDatasources(
        (prev) => (prev ?? []).map((s) =>
          s.id === source.id
            ? { ...s, is_active: true, last_test_status: 'success', last_tested_at: new Date().toISOString() }
            : s,
        ),
        { revalidate: false },
      );
    } catch {
      mutateDatasources(
        (prev) => (prev ?? []).map((s) =>
          s.id === source.id
            ? { ...s, last_test_status: 'failed', last_tested_at: new Date().toISOString() }
            : s,
        ),
        { revalidate: false },
      );
    } finally {
      setRetestingId(null);
    }
  }

  async function handleDelete(
    e: React.MouseEvent,
    source: DatasourceRecord,
  ) {
    e.stopPropagation();
    setConfirmDelete(source);
  }

  async function doDelete(source: DatasourceRecord) {
    setConfirmDelete(null);
    setDeletingId(source.id);
    try {
      await triggerDelete(source.id);
      mutateDatasources(
        (prev) => (prev ?? []).filter((s) => s.id !== source.id),
        { revalidate: false },
      );
      if (selectedSource?.id === source.id) setSelectedSource(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete connection');
    } finally {
      setDeletingId(null);
    }
  }

  if (selectedSource && selectedTable) {
    return (
      <TableAnnotationView
        source={selectedSource}
        table={selectedTable}
        schema={selectedSource.default_schema}
        onBack={() => setSelectedTable(null)}
      />
    );
  }

  if (selectedSource) {
    return (
      <TableBrowserView
        source={selectedSource}
        onBack={() => setSelectedSource(null)}
        onRetested={handleRetested}
        onTableSelected={(obj) => setSelectedTable(obj)}
      />
    );
  }

  return (
    <div className="page-inner fade-up">
      <div className="eyebrow">Connections</div>
      <h1 className="section-title" style={{ margin: '4px 0 4px' }}>
        Data Source
      </h1>
      <p className="muted" style={{ margin: '0 0 24px', fontSize: 14 }}>
        Connect a database to start generating insights. InsightX connects to
        one source at a time.
      </p>

      <h3
        style={{
          fontSize: 14,
          margin: '0 0 13px',
          color: 'var(--text-muted)',
        }}
      >
        Add a connection
      </h3>
      <div className="conn-cards">
        {DB_TYPES.map((db) => (
          <div
            className="card conn-card"
            key={db.id}
            onClick={() => setCredDb(db)}
          >
            <div className="between" style={{ alignItems: 'flex-start' }}>
              <DBLogo
                slug={db.id}
                size={46}
                radius={12}
                letter={db.letter}
                color={db.color}
              />
              <span className="pill">{db.tag}</span>
            </div>
            <h3>{db.name}</h3>
            <p className="blurb">{db.blurb}</p>
            <div className="conn-cta">
              Enter credentials <Icon name="chevronR" />
            </div>
          </div>
        ))}
      </div>

      <div className="between" style={{ margin: '34px 0 13px' }}>
        <h3 style={{ fontSize: 14, margin: 0, color: 'var(--text-muted)' }}>
          Connected sources
          {!loadingList && (
            <span className="pill" style={{ marginLeft: 6 }}>
              {datasources.length}
            </span>
          )}
        </h3>
      </div>

      {loadingList && (
        <div
          className="card card-pad"
          style={{
            textAlign: 'center',
            color: 'var(--text-faint)',
          }}
        >
          Loading…
        </div>
      )}

      {!loadingList && !listFetchError && datasources.length === 0 && (
        <div className="empty-state">
          <div className="es-ic">
            <Icon name="database" size={22} />
          </div>
          <h3>No connections yet</h3>
          <p>Add your first database above to start generating insights.</p>
        </div>
      )}

      {!loadingList && !listFetchError && datasources.length > 0 && (
        <div className="card">
          {datasources.map((source) => {
            const m =
              ENGINE_DISPLAY[source.engine] ??
              {
                slug: 'oracle',
                letter: '?',
                color: '#888',
                label: source.engine,
              };
            const isActive = source.is_active !== false;
            const statusClass = !isActive
              ? 'pill-amber dot'
              : source.last_test_status === 'success'
                ? 'pill-green dot'
                : source.last_test_status === 'failed'
                  ? 'pill-red dot'
                  : '';
            const statusLabel = !isActive
              ? 'Inactive'
              : source.last_test_status === 'success'
                ? 'Connected'
                : source.last_test_status === 'failed'
                  ? 'Failed'
                  : 'Not tested';
            const isDeleting = deletingId === source.id;
            const isDeactivating = deactivatingId === source.id;
            const isRetesting = retestingId === source.id;
            const isBusy = isDeleting || isDeactivating || isRetesting;

            return (
              <div
                className="connected-row"
                key={source.id}
                onClick={() => !isBusy && setSelectedSource(source)}
                style={{ opacity: isDeleting ? 0.5 : 1 }}
              >
                <div style={!isActive ? { filter: 'grayscale(0.6)', opacity: 0.7 } : undefined}>
                  <DBLogo
                    slug={m.slug}
                    size={38}
                    radius={10}
                    letter={m.letter}
                    color={m.color}
                  />
                </div>
                <div className="connected-meta">
                  <b>{source.name}</b>
                  <div className="host">
                    {source.host}:{source.port}/{source.database_name}
                    <span className="faint" style={{ marginLeft: 6 }}>
                      · schema:{' '}
                      <span className="mono">
                        {source.default_schema}
                      </span>
                    </span>
                  </div>
                </div>
                <div style={{ textAlign: 'right', flexShrink: 0 }}>
                  <div
                    className="faint"
                    style={{ fontSize: 11.5, fontWeight: 600 }}
                  >
                    {m.label}
                  </div>
                  {source.last_tested_at && (
                    <div
                      className="faint"
                      style={{ fontSize: 11, marginTop: 2 }}
                    >
                      {new Date(source.last_tested_at).toLocaleDateString()}
                    </div>
                  )}
                </div>
                <span className={`pill ${statusClass}`}>
                  {statusLabel}
                </span>
                {/* Re-test button — reactivates inactive connections on success */}
                <button
                  className="btn btn-ghost btn-sm"
                  title="Re-test connection"
                  disabled={isBusy}
                  onClick={(e) => handleRetestFromList(e, source)}
                  style={{ flexShrink: 0 }}
                >
                  <Icon name="refresh" size={13} />
                  {isRetesting ? 'Testing…' : 'Re-test'}
                </button>
                {/* Deactivate — only shown when connection is active */}
                {isActive && (
                  <button
                    className="btn btn-ghost btn-sm"
                    title="Deactivate connection"
                    disabled={isBusy}
                    onClick={(e) => handleDeactivate(e, source)}
                    style={{ flexShrink: 0, color: 'var(--text-faint)' }}
                  >
                    <Icon name="pause" size={13} />
                    {isDeactivating ? '…' : 'Deactivate'}
                  </button>
                )}
                <button
                  className="icon-btn"
                  title="Remove connection"
                  disabled={isBusy}
                  onClick={(e) => handleDelete(e, source)}
                  style={{
                    color: 'var(--text-faint)',
                    padding: '4px 6px',
                    borderRadius: 6,
                    border: '1px solid transparent',
                    background: 'none',
                    cursor: 'pointer',
                    flexShrink: 0,
                    transition: 'color 0.15s, border-color 0.15s',
                  }}
                  onMouseEnter={(e) => {
                    const btn = e.currentTarget as HTMLButtonElement;
                    btn.style.color = 'var(--danger)';
                    btn.style.borderColor = 'var(--danger)';
                  }}
                  onMouseLeave={(e) => {
                    const btn = e.currentTarget as HTMLButtonElement;
                    btn.style.color = 'var(--text-faint)';
                    btn.style.borderColor = 'transparent';
                  }}
                >
                  {isDeleting ? '…' : <Icon name="x" size={13} />}
                </button>
                <Icon
                  name="chevronR"
                  size={16}
                  style={{ color: 'var(--text-faint)' }}
                />
              </div>
            );
          })}
        </div>
      )}

      {credDb && (
        <CredentialModal
          db={credDb}
          onClose={() => setCredDb(null)}
          onCreated={() => mutateDatasources()}
        />
      )}

      {confirmDelete && (
        <ConfirmModal
          title="Remove connection"
          message={
            <>
              Remove{' '}
              <strong style={{ color: 'var(--text)' }}>
                {confirmDelete.name}
              </strong>
              ? This will permanently delete the connection and its encrypted
              credentials. This cannot be undone.
            </>
          }
          confirmLabel="Remove"
          variant="danger"
          onConfirm={() => doDelete(confirmDelete)}
          onCancel={() => setConfirmDelete(null)}
        />
      )}
    </div>
  );
}
