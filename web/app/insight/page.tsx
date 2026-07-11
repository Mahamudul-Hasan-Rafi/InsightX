"use client";
/**
 * web/app/insight/page.tsx
 * ─────────────────────────
 * M3: NL-to-SQL chat interface.
 *
 * CHANGES from placeholder:
 *   - Replaced the 1500ms setTimeout TODO with real API calls to the M3 backend.
 *   - Added datasource + schema selector (the "source chip" is now functional).
 *   - SQL preview is toggled by SqlBlock (already in the codebase).
 *   - Confidence indicator shows row count + execution time.
 *   - Thumbs up / thumbs down feedback buttons.
 *   - Error messages surface inline.
 *
 * FLOW:
 *   1.  User opens the page → LoadSourceModal asks them to pick a datasource + schema.
 *   2.  On first use for a schema → calls POST /nl-query/{id}/index (background).
 *   3.  User types question → POST /nl-query/{id}/query → get SQL + results + narrative.
 *   4.  Results rendered: narrative text, result table, collapsible SQL block.
 *   5.  Feedback buttons record is_correct via POST /nl-query/{id}/feedback/{qid}.
 */

import { useState, useEffect, useRef, Suspense, useCallback } from "react";
import Icon from "@/app/component/Icon";
import BarChart from "@/app/component/BarChart";
import LineChart from "@/app/component/LineChart";
import SqlBlock from "@/app/component/SqlBlock";
import { get, post } from "@/lib/utils/fetch.utils";
import {
  getDatasources,
  getDatasourceSchema,
  postNLQuery,
  postIndexSchema,
  postNLQueryFeedback,
} from "@/config/url.config";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Datasource {
  id: string;
  name: string;
  engine: string;
  host: string;
  port: number;
  database_name: string;
  default_schema: string | null;
  last_test_status: string | null;
}

interface SchemaInfo {
  name: string;
}

interface NLQueryResponse {
  query_id: string;
  question: string;
  sql: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  exec_ms: number;
  narrative: string;
  tables_used: string[];
  model_used: string | null;
}

interface Message {
  role: "user" | "assistant";
  text: string;
  queryResult?: NLQueryResponse;
  error?: string;
}

// ---------------------------------------------------------------------------
// Source selector modal
// ---------------------------------------------------------------------------

interface SourceSelectorProps {
  onSelect: (ds: Datasource, schema: string) => void;
}

function SourceSelector({ onSelect }: SourceSelectorProps) {
  const [datasources, setDatasources] = useState<Datasource[]>([]);
  const [selected, setSelected] = useState<Datasource | null>(null);
  const [schemas, setSchemas] = useState<string[]>([]);
  const [schema, setSchema] = useState("");
  const [loading, setLoading] = useState(true);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<{ data: Datasource[] }>(getDatasources)
      .then((r) => {
        const active = r.data.filter((d) => d.last_test_status === "success");
        setDatasources(active);
        if (active.length === 1) setSelected(active[0]);
      })
      .catch((e) => setError(e?.message ?? "Failed to load datasources"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setSchemaLoading(true);
    setSchema(selected.default_schema ?? "");
    get<{ schemas: { name: string }[] }>(getDatasourceSchema(selected.id))
      .then((r) => setSchemas(r.schemas?.map((s) => s.name) ?? []))
      .catch(() =>
        setSchemas(selected.default_schema ? [selected.default_schema] : []),
      )
      .finally(() => setSchemaLoading(false));
  }, [selected]);

  if (loading) {
    return (
      <div className="chat-empty fade-up" style={{ textAlign: "center" }}>
        <div className="thinking">
          Loading data sources{" "}
          <span className="dots">
            <i />
            <i />
            <i />
          </span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="chat-empty fade-up">
        <div className="es-ic">
          <Icon name="warning" size={22} />
        </div>
        <h3>Could not load data sources</h3>
        <p>{error}</p>
      </div>
    );
  }

  if (datasources.length === 0) {
    return (
      <div className="chat-empty fade-up">
        <div className="es-ic">
          <Icon name="db" size={22} />
        </div>
        <h3>No active data sources</h3>
        <p>
          Go to <strong>Data Source</strong> in the sidebar, connect a database,
          and test it before returning here.
        </p>
      </div>
    );
  }

  return (
    <div className="chat-empty fade-up">
      <div className="hi">
        <div className="spark">
          <Icon name="sparkle" size={22} />
        </div>
        <div>
          <h2>Pick a data source</h2>
          <p className="lead" style={{ marginTop: 4 }}>
            Select which connected database to query.
          </p>
        </div>
      </div>

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 10,
          width: "100%",
          maxWidth: 480,
          margin: "0 auto",
        }}
      >
        {/* Datasource list */}
        <div
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: "var(--text-faint)",
            textTransform: "uppercase",
            letterSpacing: "0.06em",
          }}
        >
          Database
        </div>
        {datasources.map((ds) => (
          <button
            key={ds.id}
            className={
              "card card-pad" + (selected?.id === ds.id ? " border-accent" : "")
            }
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              cursor: "pointer",
              textAlign: "left",
              border:
                selected?.id === ds.id ? "2px solid var(--accent)" : undefined,
              background:
                selected?.id === ds.id ? "var(--accent-soft)" : undefined,
            }}
            onClick={() => setSelected(ds)}
          >
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 8,
                background: "var(--surface-3)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Icon name="db" size={18} />
            </div>
            <div>
              <div style={{ fontWeight: 600, fontSize: 14 }}>{ds.name}</div>
              <div
                style={{
                  fontSize: 12,
                  color: "var(--text-faint)",
                  fontFamily: "var(--font-mono)",
                }}
              >
                {ds.engine} · {ds.host}/{ds.database_name}
              </div>
            </div>
            {selected?.id === ds.id && (
              <Icon
                name="check"
                size={16}
                style={{ marginLeft: "auto", color: "var(--accent)" }}
              />
            )}
          </button>
        ))}

        {/* Schema selector */}
        {selected && (
          <>
            <div
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: "var(--text-faint)",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                marginTop: 8,
              }}
            >
              Schema
            </div>
            {schemaLoading ? (
              <div style={{ fontSize: 13, color: "var(--text-faint)" }}>
                Loading schemas…
              </div>
            ) : (
              <select
                className="input"
                value={schema}
                onChange={(e) => setSchema(e.target.value)}
                style={{ fontSize: 14 }}
              >
                <option value="">— choose schema —</option>
                {schemas.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            )}
          </>
        )}

        <button
          className="btn btn-primary"
          disabled={!selected || !schema}
          onClick={() => selected && schema && onSelect(selected, schema)}
          style={{ marginTop: 8 }}
        >
          <Icon name="sparkle" size={14} /> Start querying
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Query result card
// ---------------------------------------------------------------------------

function QueryResultCard({
  result,
  onFeedback,
}: {
  result: NLQueryResponse;
  onFeedback: (queryId: string, isCorrect: boolean) => void;
}) {
  const [feedbackGiven, setFeedbackGiven] = useState<boolean | null>(null);

  const handleFeedback = (isCorrect: boolean) => {
    setFeedbackGiven(isCorrect);
    onFeedback(result.query_id, isCorrect);
  };

  return (
    <div className="insight-body fade-up">
      {/* Narrative */}
      <div className="insight-narrative">{result.narrative}</div>

      {/* Result table */}
      {result.columns.length > 0 && (
        <div className="insight-card">
          <div className="insight-card-head">
            <Icon name="table" />
            Result set
            <span className="pill" style={{ marginLeft: 8 }}>
              {result.row_count.toLocaleString()} rows
            </span>
            <div className="toolbar" style={{ marginLeft: "auto" }}>
              <button
                className="icon-btn"
                title="Download CSV"
                onClick={() => downloadCSV(result)}
              >
                <Icon name="download" size={15} />
              </button>
            </div>
          </div>
          <div
            className="insight-table"
            style={{ maxHeight: 380, overflowY: "auto" }}
          >
            <table>
              <thead>
                <tr>
                  {result.columns.map((c, i) => (
                    <th key={i}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.slice(0, 200).map((row, i) => (
                  <tr key={i}>
                    {(row as unknown[]).map((cell, j) => (
                      <td key={j}>{cell == null ? "—" : String(cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {result.rows.length > 200 && (
              <div
                style={{
                  padding: "8px 12px",
                  fontSize: 12,
                  color: "var(--text-faint)",
                }}
              >
                Showing first 200 of {result.row_count.toLocaleString()} rows.
              </div>
            )}
          </div>
        </div>
      )}

      {/* SQL block */}
      <SqlBlock sql={result.sql} />

      {/* Meta row */}
      <div className="insight-meta">
        <span>
          <Icon name="table" /> {result.row_count.toLocaleString()} rows
        </span>
        <span>
          <Icon name="clock" /> {(result.exec_ms / 1000).toFixed(2)}s
        </span>
        {result.model_used && (
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
            {result.model_used}
          </span>
        )}
        {/* Feedback */}
        {feedbackGiven === null ? (
          <span
            style={{
              marginLeft: "auto",
              display: "flex",
              gap: 6,
              alignItems: "center",
            }}
          >
            <span style={{ fontSize: 12, color: "var(--text-faint)" }}>
              Was this correct?
            </span>
            <button
              className="btn btn-subtle btn-sm"
              onClick={() => handleFeedback(true)}
              title="Correct answer"
            >
              <Icon name="check" size={13} /> Yes
            </button>
            <button
              className="btn btn-subtle btn-sm"
              onClick={() => handleFeedback(false)}
              title="Incorrect answer"
            >
              <Icon name="x" size={13} /> No
            </button>
          </span>
        ) : (
          <span
            style={{
              marginLeft: "auto",
              fontSize: 12,
              color: feedbackGiven ? "var(--success)" : "var(--text-faint)",
            }}
          >
            {feedbackGiven ? "✓ Marked as correct" : "✗ Marked as incorrect"}
          </span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CSV download helper
// ---------------------------------------------------------------------------

function downloadCSV(result: NLQueryResponse) {
  const header = result.columns.join(",");
  const body = result.rows
    .map((row) =>
      (row as unknown[])
        .map((cell) => {
          const s = cell == null ? "" : String(cell);
          return s.includes(",") || s.includes('"')
            ? `"${s.replace(/"/g, '""')}"`
            : s;
        })
        .join(","),
    )
    .join("\n");
  const blob = new Blob([header + "\n" + body], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `insightx_${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Main chat component
// ---------------------------------------------------------------------------

function InsightChat() {
  const [datasource, setDatasource] = useState<Datasource | null>(null);
  const [schema, setSchema] = useState<string>("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const [indexing, setIndexing] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  // Scroll to bottom whenever messages or thinking state changes.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages, thinking]);

  // ── Source selection ──────────────────────────────────────────────────────

  const handleSourceSelect = useCallback(
    async (ds: Datasource, schemaName: string) => {
      setDatasource(ds);
      setSchema(schemaName);
      setMessages([]);

      // Trigger background indexing for this schema so /query works immediately.
      // Fire-and-forget: the user doesn't need to wait for indexing to complete.
      setIndexing(true);
      try {
        await post(postIndexSchema(ds.id), { schema_name: schemaName });
      } catch (e) {
        // Indexing failure is non-fatal — /query will fall back to un-indexed mode.
        console.warn("[InsightX] Schema indexing failed:", e);
      } finally {
        setIndexing(false);
      }
    },
    [],
  );

  // ── Send a query ──────────────────────────────────────────────────────────

  const send = useCallback(
    async (text?: string) => {
      const q = (text ?? input).trim();
      if (!q || thinking || !datasource || !schema) return;

      setMessages((m) => [...m, { role: "user", text: q }]);
      setInput("");
      if (taRef.current) taRef.current.style.height = "auto";
      setThinking(true);

      try {
        const result = await post<NLQueryResponse, { schema_name: string; question: string }>(
          postNLQuery(datasource.id),
          { schema_name: schema, question: q },
        );
        setMessages((m) => [
          ...m,
          {
            role: "assistant",
            text: result.narrative,
            queryResult: result,
          },
        ]);
      } catch (err: unknown) {
        const message =
          err instanceof Error
            ? err.message
            : typeof err === "object" && err !== null && "detail" in err
              ? String((err as { detail: unknown }).detail)
              : "An unexpected error occurred.";

        setMessages((m) => [
          ...m,
          {
            role: "assistant",
            text: "",
            error: message,
          },
        ]);
      } finally {
        setThinking(false);
      }
    },
    [input, thinking, datasource, schema],
  );

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
  };

  const handleFeedback = async (queryId: string, isCorrect: boolean) => {
    if (!datasource) return;
    try {
      await post(postNLQueryFeedback(datasource.id, queryId), {
        is_correct: isCorrect,
      });
    } catch (e) {
      console.warn("[InsightX] Feedback failed:", e);
    }
  };

  const handleChangeSource = () => {
    setDatasource(null);
    setSchema("");
    setMessages([]);
  };

  // ── Render ────────────────────────────────────────────────────────────────

  if (!datasource || !schema) {
    return (
      <div className="insight-page">
        <div className="chat-scroll" ref={scrollRef}>
          <SourceSelector onSelect={handleSourceSelect} />
        </div>
        <div
          className="composer-wrap"
          style={{ opacity: 0.4, pointerEvents: "none" }}
        >
          <div className="composer">
            <div className="composer-box">
              <textarea
                rows={1}
                placeholder="Select a data source first…"
                disabled
              />
              <div className="composer-bar">
                <button className="source-chip" disabled>
                  <span className="dot-g" /> Select a data source
                  <Icon name="chevronD" />
                </button>
              </div>
            </div>
            <p className="composer-hint">
              InsightX reads your annotated schema &amp; glossary. Always verify
              figures before reporting.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const empty = messages.length === 0 && !thinking;

  return (
    <div className="insight-page">
      <div className="chat-scroll" ref={scrollRef}>
        {empty ? (
          <div className="chat-empty fade-up">
            <div className="hi">
              <div className="spark">
                <Icon name="sparkle" size={22} />
              </div>
              <div>
                <h2>Ask your data</h2>
                {indexing && (
                  <p
                    style={{
                      fontSize: 13,
                      color: "var(--text-faint)",
                      marginTop: 4,
                    }}
                  >
                    Indexing schema <strong>{schema}</strong>…
                    <span className="dots">
                      <i />
                      <i />
                      <i />
                    </span>
                  </p>
                )}
              </div>
            </div>
            <p className="lead">
              Query <strong>{datasource.name}</strong> / <code>{schema}</code>{" "}
              in plain language. InsightX writes the SQL, runs it, and explains
              the result.
            </p>
            <div className="suggest-grid">
              {[
                {
                  icon: "chart",
                  text: "Show total deposits by branch this month",
                },
                {
                  icon: "table",
                  text: "List top 10 customers by loan balance",
                },
                {
                  icon: "coins",
                  text: "What are the NPL ratios by sector this quarter?",
                },
                {
                  icon: "trend",
                  text: "Show me monthly transaction volume for the last 6 months",
                },
              ].map((s, i) => (
                <button
                  className="suggest-card"
                  key={i}
                  onClick={() => send(s.text)}
                >
                  <span className="si">
                    <Icon name={s.icon as never} />
                  </span>
                  {s.text}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="chat-col">
            {messages.map((m, i) =>
              m.role === "user" ? (
                <div className="msg-user fade-up" key={i}>
                  <div className="bubble">{m.text}</div>
                </div>
              ) : (
                <div className="msg-assistant" key={i}>
                  <div className="msg-avatar">
                    <Icon name="sparkle" size={17} />
                  </div>
                  {m.error ? (
                    <div className="insight-body fade-up">
                      <div
                        className="insight-narrative"
                        style={{
                          color: "var(--danger)",
                          background: "var(--danger-soft)",
                          padding: "12px 16px",
                          borderRadius: 8,
                          fontSize: 13,
                        }}
                      >
                        ⚠ {m.error}
                      </div>
                    </div>
                  ) : m.queryResult ? (
                    <QueryResultCard
                      result={m.queryResult}
                      onFeedback={handleFeedback}
                    />
                  ) : (
                    <div className="insight-narrative fade-up">{m.text}</div>
                  )}
                </div>
              ),
            )}
            {thinking && (
              <div className="msg-assistant fade-in">
                <div className="msg-avatar">
                  <Icon name="sparkle" size={17} />
                </div>
                <div className="thinking">
                  Generating SQL &amp; querying data
                  <span className="dots">
                    <i />
                    <i />
                    <i />
                  </span>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Composer */}
      <div className="composer-wrap">
        <div className="composer">
          <div className="composer-box">
            <textarea
              ref={taRef}
              value={input}
              onChange={handleChange}
              onKeyDown={handleKey}
              rows={1}
              placeholder={`Ask about ${datasource.name} / ${schema}…`}
              disabled={thinking}
            />
            <div className="composer-bar">
              {/* Functional source chip */}
              <button
                className="source-chip"
                onClick={handleChangeSource}
                title="Change data source"
              >
                <span className="dot-g" />
                {datasource.name} · {schema}
                <Icon name="chevronD" />
              </button>
              <button
                className="composer-send"
                disabled={!input.trim() || thinking}
                onClick={() => send()}
              >
                <Icon name="send" size={17} />
              </button>
            </div>
          </div>
          <p className="composer-hint">
            InsightX reads your annotated schema &amp; glossary. Always verify
            figures before reporting.
          </p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page export
// ---------------------------------------------------------------------------

export default function InsightPage() {
  return (
    <Suspense
      fallback={
        <div className="insight-page">
          <div className="chat-scroll" />
        </div>
      }
    >
      <InsightChat />
    </Suspense>
  );
}
