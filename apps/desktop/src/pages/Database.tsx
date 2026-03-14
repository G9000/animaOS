import { useState, useEffect, useRef } from "react";
import {
  api,
  type DbTableInfo,
  type DbTableData,
  type DbQueryResult,
} from "../lib/api";

type View = "tables" | "rows" | "query";

export default function Database() {
  const [view, setView] = useState<View>("tables");
  const [tables, setTables] = useState<DbTableInfo[]>([]);
  const [tableData, setTableData] = useState<DbTableData | null>(null);
  const [queryResult, setQueryResult] = useState<DbQueryResult | null>(null);
  const [sql, setSql] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const queryRef = useRef<HTMLTextAreaElement>(null);

  // Edit / delete gating
  const [editMode, setEditMode] = useState(false);
  const [editingRow, setEditingRow] = useState<number | null>(null);
  const [editValues, setEditValues] = useState<Record<string, string>>({});

  const PAGE_SIZE = 100;

  useEffect(() => {
    loadTables();
  }, []);

  // Reset editing state when edit mode is toggled off
  useEffect(() => {
    if (!editMode) {
      setEditingRow(null);
      setEditValues({});
    }
  }, [editMode]);

  async function loadTables() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.db.tables();
      setTables(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load tables");
    } finally {
      setLoading(false);
    }
  }

  async function openTable(name: string, pageNum = 0) {
    setLoading(true);
    setError(null);
    setPage(pageNum);
    setView("rows");
    setEditingRow(null);
    setEditValues({});
    try {
      const data = await api.db.tableRows(name, PAGE_SIZE, pageNum * PAGE_SIZE);
      setTableData(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load table");
    } finally {
      setLoading(false);
    }
  }

  async function runQuery() {
    if (!sql.trim()) return;
    setLoading(true);
    setError(null);
    setQueryResult(null);
    try {
      const data = await api.db.query(sql);
      setQueryResult(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Query failed");
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      runQuery();
    }
  }

  // ---------------------------------------------------------------------------
  // Row editing helpers
  // ---------------------------------------------------------------------------

  function buildConditions(row: Record<string, unknown>): Record<string, unknown> {
    if (!tableData) return {};
    const pks = tableData.primaryKeys ?? [];
    if (pks.length > 0) {
      const cond: Record<string, unknown> = {};
      for (const pk of pks) cond[pk] = row[pk];
      return cond;
    }
    // Fallback: use all columns
    return { ...row };
  }

  function startEdit(rowIndex: number, row: Record<string, unknown>) {
    setEditingRow(rowIndex);
    const vals: Record<string, string> = {};
    for (const [k, v] of Object.entries(row)) {
      vals[k] = v === null || v === undefined ? "" : String(v);
    }
    setEditValues(vals);
  }

  function cancelEdit() {
    setEditingRow(null);
    setEditValues({});
  }

  async function saveEdit(originalRow: Record<string, unknown>) {
    if (!tableData) return;
    const conditions = buildConditions(originalRow);
    const updates: Record<string, unknown> = {};
    for (const col of tableData.columns) {
      const newVal = editValues[col] ?? "";
      const oldVal =
        originalRow[col] === null || originalRow[col] === undefined
          ? ""
          : String(originalRow[col]);
      if (newVal !== oldVal) {
        updates[col] = newVal === "" ? null : newVal;
      }
    }
    if (Object.keys(updates).length === 0) {
      cancelEdit();
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await api.db.updateRow(tableData.table, conditions, updates);
      cancelEdit();
      await openTable(tableData.table, page);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Update failed");
    } finally {
      setLoading(false);
    }
  }

  async function deleteRow(row: Record<string, unknown>) {
    if (!tableData) return;
    const conditions = buildConditions(row);
    setLoading(true);
    setError(null);
    try {
      await api.db.deleteRow(tableData.table, conditions);
      await openTable(tableData.table, page);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setLoading(false);
    }
  }

  // ---------------------------------------------------------------------------
  // Render helpers
  // ---------------------------------------------------------------------------

  function renderCell(value: unknown): string {
    if (value === null || value === undefined) return "NULL";
    if (typeof value === "string" && value.length > 120)
      return value.slice(0, 120) + "…";
    return String(value);
  }

  function renderDataTable(
    columns: string[],
    rows: Record<string, unknown>[],
    options?: { editable?: boolean },
  ) {
    const editable = options?.editable && editMode;
    if (columns.length === 0) {
      return <p className="text-text-muted text-sm">No columns</p>;
    }
    return (
      <div className="overflow-auto max-h-[calc(100vh-280px)] border border-border rounded-md">
        <table className="w-full text-[12px] font-mono">
          <thead className="sticky top-0 z-10">
            <tr className="bg-bg-card border-b border-border">
              {editable && (
                <th className="px-2 py-2 text-left text-text-muted font-medium whitespace-nowrap w-[80px]">
                  Actions
                </th>
              )}
              {columns.map((col) => (
                <th
                  key={col}
                  className="px-3 py-2 text-left text-text-muted font-medium whitespace-nowrap"
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const isEditing = editable && editingRow === i;
              return (
                <tr
                  key={i}
                  className="border-b border-border/50 hover:bg-bg-card/60 transition-colors"
                >
                  {editable && (
                    <td className="px-2 py-1.5 whitespace-nowrap">
                      {isEditing ? (
                        <div className="flex gap-1">
                          <button
                            onClick={() => saveEdit(row)}
                            className="text-[10px] text-primary hover:text-primary-hover"
                          >
                            Save
                          </button>
                          <button
                            onClick={cancelEdit}
                            className="text-[10px] text-text-muted hover:text-text"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <div className="flex gap-1">
                          <button
                            onClick={() => startEdit(i, row)}
                            className="text-[10px] text-text-muted hover:text-primary transition-colors"
                          >
                            Edit
                          </button>
                          <button
                            onClick={() => deleteRow(row)}
                            className="text-[10px] text-text-muted hover:text-danger transition-colors"
                          >
                            Del
                          </button>
                        </div>
                      )}
                    </td>
                  )}
                  {columns.map((col) => (
                    <td
                      key={col}
                      className="px-3 py-1.5 whitespace-nowrap max-w-[300px] truncate"
                    >
                      {isEditing ? (
                        <input
                          type="text"
                          value={editValues[col] ?? ""}
                          onChange={(e) =>
                            setEditValues((prev) => ({
                              ...prev,
                              [col]: e.target.value,
                            }))
                          }
                          onKeyDown={(e) => {
                            if (e.key === "Enter") saveEdit(row);
                            if (e.key === "Escape") cancelEdit();
                          }}
                          className="w-full min-w-[80px] bg-bg-input border border-border rounded px-1.5 py-0.5 text-[12px] font-mono outline-none focus:border-primary/40"
                        />
                      ) : (
                        <span
                          className={
                            row[col] === null
                              ? "text-text-muted/40 italic"
                              : ""
                          }
                        >
                          {renderCell(row[col])}
                        </span>
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Views
  // ---------------------------------------------------------------------------

  function renderTablesView() {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium text-text-muted">
            {tables.length} tables
          </h2>
          <button
            onClick={loadTables}
            className="text-xs text-text-muted hover:text-text transition-colors"
          >
            Refresh
          </button>
        </div>
        <div className="grid gap-1.5">
          {tables.map((t) => (
            <button
              key={t.name}
              onClick={() => openTable(t.name)}
              className="flex items-center justify-between px-3 py-2.5 rounded-md bg-bg-card/50 hover:bg-bg-card border border-border/50 hover:border-border transition-all text-left group"
            >
              <span className="font-mono text-[13px]">{t.name}</span>
              <span className="text-xs text-text-muted group-hover:text-text transition-colors">
                {t.rowCount} rows
              </span>
            </button>
          ))}
        </div>
      </div>
    );
  }

  function renderRowsView() {
    if (!tableData) return null;
    const totalPages = Math.ceil(tableData.total / PAGE_SIZE);
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              setView("tables");
              setTableData(null);
            }}
            className="text-xs text-text-muted hover:text-text transition-colors"
          >
            ← Tables
          </button>
          <h2 className="font-mono text-sm font-medium">{tableData.table}</h2>
          <span className="text-xs text-text-muted">
            {tableData.total} rows
          </span>
          <button
            onClick={() => {
              setSql(`SELECT * FROM "${tableData.table}" LIMIT 100`);
              setView("query");
            }}
            className="ml-auto text-xs text-primary hover:text-primary-hover transition-colors"
          >
            Query this table
          </button>
        </div>

        {/* Edit mode toggle */}
        <label className="inline-flex items-center gap-2 select-none cursor-pointer">
          <input
            type="checkbox"
            checked={editMode}
            onChange={(e) => setEditMode(e.target.checked)}
            className="w-3.5 h-3.5 accent-danger cursor-pointer"
          />
          <span className="text-xs text-text-muted">
            Enable editing
          </span>
        </label>

        {renderDataTable(tableData.columns, tableData.rows, { editable: true })}
        {totalPages > 1 && (
          <div className="flex items-center gap-2 justify-center pt-1">
            <button
              disabled={page === 0}
              onClick={() => openTable(tableData.table, page - 1)}
              className="px-2 py-1 text-xs rounded bg-bg-card border border-border disabled:opacity-30 hover:bg-bg-input transition-colors"
            >
              Prev
            </button>
            <span className="text-xs text-text-muted">
              Page {page + 1} of {totalPages}
            </span>
            <button
              disabled={page >= totalPages - 1}
              onClick={() => openTable(tableData.table, page + 1)}
              className="px-2 py-1 text-xs rounded bg-bg-card border border-border disabled:opacity-30 hover:bg-bg-input transition-colors"
            >
              Next
            </button>
          </div>
        )}
      </div>
    );
  }

  function renderQueryView() {
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              setView("tables");
              setQueryResult(null);
              setError(null);
            }}
            className="text-xs text-text-muted hover:text-text transition-colors"
          >
            ← Tables
          </button>
          <h2 className="text-sm font-medium text-text-muted">SQL Query</h2>
        </div>
        <div className="relative">
          <textarea
            ref={queryRef}
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="SELECT * FROM users LIMIT 10"
            rows={4}
            className="w-full px-3 py-2.5 rounded-md bg-bg-input border border-border text-[13px] font-mono resize-y placeholder:text-text-muted/40 focus:outline-none focus:border-primary/40 transition-colors"
          />
          <div className="flex items-center justify-between mt-2">
            <span className="text-[11px] text-text-muted/50">
              Ctrl+Enter to run • SELECT, PRAGMA, EXPLAIN only
            </span>
            <button
              onClick={runQuery}
              disabled={loading || !sql.trim()}
              className="px-3 py-1.5 text-xs rounded-md bg-primary/15 text-primary hover:bg-primary/25 border border-primary/20 disabled:opacity-30 transition-all"
            >
              {loading ? "Running…" : "Run"}
            </button>
          </div>
        </div>
        {queryResult && (
          <div className="space-y-2">
            <span className="text-xs text-text-muted">
              {queryResult.rowCount} row{queryResult.rowCount !== 1 ? "s" : ""}{" "}
              returned
            </span>
            {renderDataTable(queryResult.columns, queryResult.rows)}
          </div>
        )}
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Main render
  // ---------------------------------------------------------------------------

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <header className="shrink-0 px-6 py-4 border-b border-border">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-base font-semibold tracking-tight">Database</h1>
            <p className="text-xs text-text-muted mt-0.5">
              Inspect tables and run queries on the encrypted store
            </p>
          </div>
          <div className="flex gap-1.5">
            <button
              onClick={() => {
                setView("tables");
                setError(null);
              }}
              className={`px-3 py-1.5 text-xs rounded-md border transition-all ${
                view === "tables"
                  ? "bg-bg-card border-border text-text"
                  : "border-transparent text-text-muted hover:text-text"
              }`}
            >
              Tables
            </button>
            <button
              onClick={() => {
                setView("query");
                setError(null);
              }}
              className={`px-3 py-1.5 text-xs rounded-md border transition-all ${
                view === "query"
                  ? "bg-bg-card border-border text-text"
                  : "border-transparent text-text-muted hover:text-text"
              }`}
            >
              Query
            </button>
          </div>
        </div>
      </header>

      {/* Content */}
      <div className="flex-1 overflow-auto px-6 py-4">
        {error && (
          <div className="mb-3 px-3 py-2 rounded-md bg-danger/10 border border-danger/20 text-danger text-xs">
            {error}
          </div>
        )}
        {loading && view === "tables" && tables.length === 0 ? (
          <p className="text-sm text-text-muted animate-pulse">Loading…</p>
        ) : (
          <>
            {view === "tables" && renderTablesView()}
            {view === "rows" && renderRowsView()}
            {view === "query" && renderQueryView()}
          </>
        )}
      </div>
    </div>
  );
}
