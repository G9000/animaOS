import { useState, useEffect, useMemo, useRef } from "react";
import type { DbTableInfo, DbTableData, DbQueryResult } from "@anima/api-client";
import { api } from "../lib/api";
import { Icons } from "../components/database/Icons";
import { NavButton } from "../components/database/components";
import { useLocalStorage, useColumnStats, useVirtualList, useLastSession, useQueryDraft, useColumnVisibility } from "../components/database/hooks";
import type { View, RowViewMode, ExportFormat, Bookmark, TableStats, QueryHistoryItem } from "../components/database/types";
import { Dashboard, TableList, RowViewer, SchemaView, RelationsView, QueryEditor } from "../components/database/views";
import { convertToCsv, generateInsertSQL, downloadFile, applyColumnFilters } from "../components/database/utils";
import type { ColumnFilter } from "../components/database/ColumnFilter";
import { KeyboardShortcutsHelp, useKeyboardShortcuts, ToastContainer, showSuccess, showError, showInfo } from "../components/database";

export default function Database() {
  // Password gate
  const [unlocked, setUnlocked] = useState(false);
  const [password, setPassword] = useState("");
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);

  // View state
  const [view, setView] = useState<View>("dashboard");
  const [tables, setTables] = useState<DbTableInfo[]>([]);
  const [tableData, setTableData] = useState<DbTableData | null>(null);
  const [queryResult, setQueryResult] = useState<DbQueryResult | null>(null);
  const [sql, setSql] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);

  // View modes
  const [rowViewMode, setRowViewMode] = useState<RowViewMode>("list");

  // Search & filter
  const [tableSearch, setTableSearch] = useState("");
  const [rowFilter, setRowFilter] = useState("");
  const [expandedCells, setExpandedCells] = useState<Set<string>>(new Set());

  // Schema
  const [schemaColumns, setSchemaColumns] = useState<Array<{ name: string; type: string; nullable: boolean; default: string | null; primaryKey: boolean }>>([]);
  const [schemaIndexes, setSchemaIndexes] = useState<string[]>([]);

  // Query history
  const [queryHistory, setQueryHistory] = useLocalStorage<QueryHistoryItem[]>("db-query-history", []);
  const [showHistory, setShowHistory] = useState(false);

  // Edit
  const [editMode, setEditMode] = useState(false);
  const [editingRow, setEditingRow] = useState<number | null>(null);
  const [editValues, setEditValues] = useState<Record<string, string>>({});

  // Stats
  const [stats, setStats] = useState<TableStats | null>(null);
  const { columnStats, calculateStats } = useColumnStats();

  // Selection
  const [selectedRows, setSelectedRows] = useState<Set<number>>(new Set());
  const [selectAll, setSelectAll] = useState(false);

  // Column stats toggle
  const [showColumnStats, setShowColumnStats] = useState(false);

  // Bookmarks
  const [bookmarks, setBookmarks] = useLocalStorage<Bookmark[]>("db-bookmarks", []);

  // Recent tables
  const [recentTables, setRecentTables] = useLocalStorage<string[]>("db-recent-tables", []);

  // Export
  const [showExportMenu, setShowExportMenu] = useState(false);

  // Copy feedback
  const [copiedCell, setCopiedCell] = useState<string | null>(null);

  // Column widths
  const [columnWidths, setColumnWidths] = useLocalStorage<Record<string, number>>("db-col-widths", {});
  const resizingCol = useRef<string | null>(null);
  const startX = useRef(0);
  const startWidth = useRef(0);

  // Column filters
  const [columnFilters, setColumnFilters] = useState<ColumnFilter[]>([]);

  // Virtual list (for future use)
  const { containerRef } = useVirtualList([], 32, 10);

  // Last session restoration
  const { saveSession, restoreSession } = useLastSession();

  // Query draft auto-save
  const { draft: queryDraft, updateDraft: updateQueryDraft } = useQueryDraft();

  const PAGE_SIZE = 100;

  // Initial load + session restore
  // Load draft on mount
  useEffect(() => {
    if (queryDraft && !sql) {
      setSql(queryDraft);
    }
  }, []);

  useEffect(() => {
    if (unlocked) {
      loadTables();
      calculateDashboardStats();
      
      // Restore last session
      const session = restoreSession();
      if (session) {
        if (session.lastQuery && !sql) {
          setSql(session.lastQuery);
        }
        // Could also restore lastTable here if we wanted
      }
    }
  }, [unlocked]);

  // Reset edit state
  useEffect(() => {
    if (!editMode) {
      setEditingRow(null);
      setEditValues({});
    }
  }, [editMode]);

  // Reset selection on table change
  useEffect(() => {
    setSelectedRows(new Set());
    setSelectAll(false);
  }, [tableData?.table]);

  // Auto-save query draft
  useEffect(() => {
    updateQueryDraft(sql);
  }, [sql, updateQueryDraft]);

  // Save session on view/table change
  useEffect(() => {
    if (unlocked) {
      saveSession({
        lastView: view,
        lastTable: tableData?.table,
        lastQuery: sql,
      });
    }
  }, [view, tableData?.table, sql, unlocked, saveSession]);

  const handleVerify = async () => {
    if (!password) return;
    setVerifying(true);
    setVerifyError(null);
    try {
      await api.db.verifyPassword(password);
      setUnlocked(true);
      setPassword("");
    } catch (e) {
      setVerifyError(e instanceof Error ? e.message : "Verification failed");
    } finally {
      setVerifying(false);
    }
  };

  const loadTables = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.db.tables();
      setTables(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load tables");
    } finally {
      setLoading(false);
    }
  };

  const calculateDashboardStats = async () => {
    try {
      const tableData = await api.db.tables();
      const totalRows = tableData.reduce((sum, t) => sum + t.rowCount, 0);
      const largest = tableData.reduce((max, t) => (t.rowCount > max.rowCount ? t : max), tableData[0]);
      setStats({
        totalRows,
        totalTables: tableData.length,
        largestTable: largest?.name || "—",
        recentQueries: queryHistory.length,
      });
    } catch {
      // ignore
    }
  };

  const openTable = async (name: string, pageNum = 0) => {
    setLoading(true);
    setError(null);
    setPage(pageNum);
    setEditingRow(null);
    setEditValues({});
    setRowFilter("");
    setExpandedCells(new Set());
    setSelectedRows(new Set());
    setSelectAll(false);

    setRecentTables((prev) => {
      const filtered = prev.filter((t) => t !== name);
      return [name, ...filtered].slice(0, 10);
    });

    try {
      const data = await api.db.tableRows(name, PAGE_SIZE, pageNum * PAGE_SIZE);
      setTableData(data);
      setView("rows");
      await loadSchema(name);
      calculateStats(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load table");
    } finally {
      setLoading(false);
    }
  };

  const loadSchema = async (tableName: string) => {
    try {
      const schema = await api.db.tableSchema(tableName);
      setSchemaColumns(schema.columns || []);
      setSchemaIndexes(schema.indexes || []);
    } catch {
      setSchemaColumns([]);
      setSchemaIndexes([]);
    }
  };

  const runQuery = async () => {
    if (!sql.trim()) return;
    setLoading(true);
    setError(null);
    setQueryResult(null);
    try {
      const data = await api.db.query(sql);
      setQueryResult(data);
      showSuccess(`Query returned ${data.rowCount.toLocaleString()} rows`);
      const newItem: QueryHistoryItem = {
        sql: sql.trim(),
        timestamp: Date.now(),
        rowCount: data.rowCount,
      };
      setQueryHistory((prev) => [newItem, ...prev.slice(0, 19)]);
      calculateDashboardStats();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Query failed";
      showError(msg);
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  // Filtered tables
  const filteredTables = useMemo(() => {
    if (!tableSearch.trim()) return tables;
    const search = tableSearch.toLowerCase();
    return tables.filter((t) => t.name.toLowerCase().includes(search));
  }, [tables, tableSearch]);

  // Filtered rows
  // Filter rows (search + column filters)
  const filteredRows = useMemo(() => {
    if (!tableData?.rows) return [];
    
    let rows = tableData.rows;
    
    // Apply column filters first
    if (columnFilters.length > 0) {
      rows = applyColumnFilters(rows, columnFilters);
    }
    
    // Apply text filter
    if (rowFilter.trim()) {
      const filter = rowFilter.toLowerCase();
      rows = rows.filter((row) =>
        Object.values(row).some((val) => String(val).toLowerCase().includes(filter))
      );
    }
    
    return rows;
  }, [tableData, rowFilter, columnFilters]);

  // Top tables
  const topTables = useMemo(() => {
    return [...tables].sort((a, b) => b.rowCount - a.rowCount).slice(0, 5);
  }, [tables]);

  // Foreign keys
  const foreignKeys = useMemo(() => {
    if (!tableData) return [];
    return tableData.columns
      .filter((col) => col.endsWith("_id"))
      .map((col) => {
        const targetTable = col.replace(/_id$/, "");
        const matchingTable = tables.find(
          (t) => t.name === targetTable || t.name === targetTable + "s"
        );
        return matchingTable
          ? {
              fromTable: tableData.table,
              fromColumn: col,
              toTable: matchingTable.name,
              toColumn: "id",
            }
          : null;
      })
      .filter(Boolean) as Array<{ fromTable: string; fromColumn: string; toTable: string; toColumn: string }>;
  }, [tableData, tables]);

  // Can mutate
  const canMutate = editMode && (tableData?.primaryKeys?.length ?? 0) > 0;

  // Column visibility
  const {
    visibleColumns,
    hiddenColumns,
    toggleColumn,
    showAllColumns,
    hideAllColumns,
  } = useColumnVisibility(tableData?.table || null, tableData?.columns || []);

  // Bookmarks helpers
  const addBookmark = (type: "table" | "query", name: string, value: string) => {
    const newBookmark: Bookmark = { type, name, value, timestamp: Date.now() };
    setBookmarks((prev) => [newBookmark, ...prev].slice(0, 20));
  };

  const removeBookmark = (timestamp: number) => {
    setBookmarks((prev) => prev.filter((b) => b.timestamp !== timestamp));
  };

  const isBookmarked = (type: "table" | "query", value: string) => {
    return bookmarks.some((b) => b.type === type && b.value === value);
  };

  // Row helpers
  const buildConditions = (row: Record<string, unknown>) => {
    if (!tableData) return {};
    const pks = tableData.primaryKeys ?? [];
    if (pks.length === 0) return {};
    return Object.fromEntries(pks.map((pk) => [pk, row[pk]]));
  };

  const startEdit = (rowIndex: number, row: Record<string, unknown>) => {
    setEditingRow(rowIndex);
    setEditValues(Object.fromEntries(Object.entries(row).map(([k, v]) => [k, v == null ? "" : String(v)])));
  };

  const cancelEdit = () => {
    setEditingRow(null);
    setEditValues({});
  };

  const saveEdit = async (originalRow: Record<string, unknown>) => {
    if (!tableData) return;
    const conditions = buildConditions(originalRow);
    const updates = Object.fromEntries(
      Object.entries(editValues).filter(([col, val]) => {
        const oldVal = originalRow[col] == null ? "" : String(originalRow[col]);
        return val !== oldVal;
      })
    );
    if (Object.keys(updates).length === 0) {
      cancelEdit();
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await api.db.updateRow(tableData.table, conditions, updates);
      showSuccess(`Updated ${Object.keys(updates).length} field(s)`);
      cancelEdit();
      await openTable(tableData.table, page);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Update failed";
      showError(msg);
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const deleteRow = async (row: Record<string, unknown>) => {
    if (!tableData) return;
    const conditions = buildConditions(row);
    setLoading(true);
    setError(null);
    try {
      await api.db.deleteRow(tableData.table, conditions);
      showSuccess("Row deleted successfully");
      await openTable(tableData.table, page);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Delete failed";
      showError(msg);
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  // Import rows
  const importRows = async (rows: Record<string, unknown>[]) => {
    if (!tableData) return;
    // TODO: Insert each row via API
    // For now, just refresh the table
    console.log(`Would import ${rows.length} rows into ${tableData.table}`);
    await openTable(tableData.table, page);
  };

  // Selection helpers
  const toggleRowSelection = (index: number) => {
    setSelectedRows((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectAll) {
      setSelectedRows(new Set());
    } else {
      setSelectedRows(new Set(filteredRows.map((_, i) => i)));
    }
    setSelectAll(!selectAll);
  };

  const deleteSelectedRows = async () => {
    if (!tableData || selectedRows.size === 0) return;
    if (!confirm(`Delete ${selectedRows.size} selected rows?`)) return;
    setLoading(true);
    for (const idx of selectedRows) {
      const row = filteredRows[idx];
      if (!row) continue;
      try {
        await api.db.deleteRow(tableData.table, buildConditions(row));
      } catch {
        // ignore individual failures
      }
    }
    setSelectedRows(new Set());
    setSelectAll(false);
    await openTable(tableData.table, page);
    setLoading(false);
  };

  // Copy helper
  const copyToClipboard = async (text: string, identifier: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedCell(identifier);
      showInfo("Copied to clipboard", 2000);
      setTimeout(() => setCopiedCell(null), 1500);
    } catch {
      showError("Failed to copy");
    }
  };

  // Cell expand helper
  const toggleCellExpand = (rowIdx: number, col: string) => {
    const key = `${rowIdx}-${col}`;
    setExpandedCells((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  // Column resize
  const startResize = (e: React.MouseEvent, col: string) => {
    e.preventDefault();
    resizingCol.current = col;
    startX.current = e.clientX;
    startWidth.current = columnWidths[col] || 150;

    const handleMove = (e: MouseEvent) => {
      if (!resizingCol.current) return;
      const diff = e.clientX - startX.current;
      const newWidth = Math.max(50, startWidth.current + diff);
      setColumnWidths((prev) => ({ ...prev, [resizingCol.current!]: newWidth }));
    };

    const handleUp = () => {
      resizingCol.current = null;
      document.removeEventListener("mousemove", handleMove);
      document.removeEventListener("mouseup", handleUp);
    };

    document.addEventListener("mousemove", handleMove);
    document.addEventListener("mouseup", handleUp);
  };

  // Export
  const exportData = (format: ExportFormat) => {
    if (!tableData) return;
    let content = "";
    let filename = "";
    let mimeType = "";

    switch (format) {
      case "csv":
        content = convertToCsv({ columns: tableData.columns, rows: filteredRows, rowCount: filteredRows.length });
        filename = `${tableData.table}.csv`;
        mimeType = "text/csv";
        break;
      case "json":
        content = JSON.stringify(filteredRows, null, 2);
        filename = `${tableData.table}.json`;
        mimeType = "application/json";
        break;
      case "sql":
        content = generateInsertSQL(tableData.table, tableData.columns, filteredRows);
        filename = `${tableData.table}.sql`;
        mimeType = "text/plain";
        break;
    }

    downloadFile(content, filename, mimeType);
    showSuccess(`Exported ${filteredRows.length.toLocaleString()} rows as ${format.toUpperCase()}`);
    setShowExportMenu(false);
  };

  // Keyboard shortcuts
  useKeyboardShortcuts({
    onRunQuery: view === "query" ? runQuery : undefined,
    onFocusSearch: () => {
      const searchInput = document.querySelector('input[type="text"]') as HTMLInputElement;
      searchInput?.focus();
    },
  });

  // Unlock screen
  if (!unlocked) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="w-full max-w-sm space-y-4">
          <div className="text-center space-y-1">
            <div className="w-12 h-12 mx-auto mb-4 rounded-none bg-primary/10 flex items-center justify-center text-primary">
              <Icons.Schema />
            </div>
            <h1 className="text-lg font-semibold">Database Viewer</h1>
            <p className="text-xs text-muted-foreground">Enter your password to view decrypted data</p>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleVerify();
            }}
            className="space-y-3"
          >
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              autoFocus
              className="w-full bg-input border border-border rounded-none px-4 py-3 text-sm placeholder:text-muted-foreground/50 outline-none focus:border-primary transition-colors"
            />
            {verifyError && <p className="text-xs text-destructive px-1">{verifyError}</p>}
            <button
              type="submit"
              disabled={verifying || !password}
              className="w-full px-4 py-3 bg-primary text-white rounded-none text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              {verifying ? "Verifying…" : "Unlock Database"}
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <header className="shrink-0 px-6 py-3 border-b border-border bg-card/50">
        <div className="flex items-center justify-between">
          {/* Left: Logo & Title */}
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-none bg-primary/10 flex items-center justify-center text-primary">
              <Icons.Schema />
            </div>
            <div>
              <h1 className="text-base font-semibold">Database Viewer</h1>
              <p className="text-[11px] text-muted-foreground">
                {tables.length} tables • {stats?.totalRows?.toLocaleString() ?? "—"} rows
              </p>
            </div>
          </div>

          {/* Center: Main Navigation */}
          <nav className="flex items-center gap-1 bg-input/50 p-1 rounded-none">
            <NavButton 
              active={view === "dashboard"} 
              onClick={() => setView("dashboard")} 
              icon={<Icons.Dashboard />}
            >
              Dashboard
            </NavButton>
            <NavButton 
              active={view === "tables" || view === "rows"} 
              onClick={() => setView("tables")} 
              icon={<Icons.Table />}
            >
              Tables
            </NavButton>
            <NavButton 
              active={view === "query"} 
              onClick={() => setView("query")} 
              icon={<Icons.Eye />}
            >
              SQL Query
            </NavButton>
          </nav>

          {/* Right: Actions */}
          <div className="flex items-center gap-2">
            {/* Refresh button */}
            <button
              onClick={loadTables}
              disabled={loading}
              className="p-2 text-muted-foreground hover:text-foreground hover:bg-input rounded-none transition-colors disabled:opacity-50"
              title="Refresh tables"
            >
              <Icons.Refresh />
            </button>

            <div className="w-px h-6 bg-border" />

            {/* Help & Shortcuts */}
            <KeyboardShortcutsHelp />

            {/* Settings/Menu placeholder */}
            <button
              className="p-2 text-muted-foreground hover:text-foreground hover:bg-input rounded-none transition-colors"
              title="Settings"
            >
              <Icons.Settings />
            </button>
          </div>
        </div>

        {/* Breadcrumb / Context bar */}
        {tableData && (
          <div className="flex items-center gap-2 mt-3 pt-3 border-t border-border/50">
            <button
              onClick={() => setView("tables")}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              Tables
            </button>
            <Icons.ChevronRight />
            <span className="text-xs font-mono font-medium">{tableData.table}</span>
            {view === "schema" && (
              <>
                <Icons.ChevronRight />
                <span className="text-xs text-muted-foreground">Schema</span>
              </>
            )}
            {view === "relations" && (
              <>
                <Icons.ChevronRight />
                <span className="text-xs text-muted-foreground">Relations</span>
              </>
            )}
          </div>
        )}
      </header>

      {/* Content */}
      <div className="flex-1 overflow-auto px-6 py-5">
        {error && (
          <div className="mb-4 px-4 py-3 rounded-none bg-destructive/10 border border-destructive/20 text-destructive text-sm flex items-center gap-2">
            <Icons.Warning />
            {error}
          </div>
        )}

        {loading && view === "tables" && tables.length === 0 ? (
          <div className="flex items-center justify-center h-32">
            <div className="animate-pulse text-muted-foreground">Loading database…</div>
          </div>
        ) : (
          <>
            {view === "dashboard" && (
              <Dashboard
                tables={tables}
                stats={stats}
                recentTables={recentTables}
                bookmarks={bookmarks}
                topTables={topTables}
                onOpenTable={openTable}
                onSetView={setView}
                onRemoveBookmark={removeBookmark}
                isBookmarked={isBookmarked}
              />
            )}

            {view === "tables" && (
              <TableList
                tables={tables}
                filteredTables={filteredTables}
                tableSearch={tableSearch}
                bookmarks={bookmarks}
                onOpenTable={openTable}
                onSetTableSearch={setTableSearch}
                onLoadTables={loadTables}
                onSetSql={setSql}
                onSetView={setView}
                onAddBookmark={addBookmark}
                onRemoveBookmark={removeBookmark}
                isBookmarked={isBookmarked}
              />
            )}

            {view === "rows" && tableData && (
              <RowViewer
                tableData={tableData}
                filteredRows={filteredRows}
                schemaColumns={schemaColumns}
                columnStats={columnStats}
                columnWidths={columnWidths}
                bookmarks={bookmarks}
                rowViewMode={rowViewMode}
                editMode={editMode}
                editingRow={editingRow}
                editValues={editValues}
                expandedCells={expandedCells}
                rowFilter={rowFilter}
                selectedRows={selectedRows}
                selectAll={selectAll}
                showColumnStats={showColumnStats}
                showExportMenu={showExportMenu}
                copiedCell={copiedCell}
                page={page}
                pageSize={PAGE_SIZE}
                onSetView={setView}
                onSetTableData={setTableData}
                onSetRowViewMode={setRowViewMode}
                onSetRowFilter={setRowFilter}
                onSetEditMode={setEditMode}
                onToggleCellExpand={toggleCellExpand}
                onCopyToClipboard={copyToClipboard}
                onOpenTable={openTable}
                onStartEdit={startEdit}
                onCancelEdit={cancelEdit}
                onSaveEdit={saveEdit}
                onDeleteRow={deleteRow}
                onToggleRowSelection={toggleRowSelection}
                onToggleSelectAll={toggleSelectAll}
                onDeleteSelectedRows={deleteSelectedRows}
                onSetShowColumnStats={setShowColumnStats}
                onExportData={exportData}
                onSetShowExportMenu={setShowExportMenu}
                onStartResize={startResize}
                onAddBookmark={addBookmark}
                onRemoveBookmark={removeBookmark}
                onSetEditValues={setEditValues}
                isBookmarked={isBookmarked}
                canMutate={canMutate}
                containerRef={containerRef}
                columnFilters={columnFilters}
                onAddColumnFilter={(filter) => setColumnFilters((prev) => [...prev, filter])}
                onRemoveColumnFilter={(idx) => setColumnFilters((prev) => prev.filter((_, i) => i !== idx))}
                onClearColumnFilters={() => setColumnFilters([])}
                onImportRows={importRows}
                visibleColumns={visibleColumns}
                hiddenColumns={hiddenColumns}
                onToggleColumnVisibility={toggleColumn}
                onShowAllColumns={showAllColumns}
                onHideAllColumns={hideAllColumns}
              />
            )}

            {view === "schema" && tableData && (
              <SchemaView tableData={tableData} schemaColumns={schemaColumns} schemaIndexes={schemaIndexes} onSetView={setView} />
            )}

            {view === "relations" && tableData && (
              <RelationsView
                tableData={tableData}
                tables={tables}
                foreignKeys={foreignKeys}
                onSetView={setView}
                onOpenTable={openTable}
              />
            )}

            {view === "query" && (
              <QueryEditor
                sql={sql}
                queryResult={queryResult}
                queryHistory={queryHistory}
                bookmarks={bookmarks}
                showHistory={showHistory}
                loading={loading}
                onSetSql={setSql}
                onRunQuery={runQuery}
                onSetShowHistory={setShowHistory}
                onSetQueryHistory={setQueryHistory}
                onSetView={setView}
                onAddBookmark={addBookmark}
                onRemoveBookmark={removeBookmark}
                isBookmarked={isBookmarked}
              />
            )}
          </>
        )}
      </div>

      {/* Toast notifications */}
      <ToastContainer />
    </div>
  );
}
