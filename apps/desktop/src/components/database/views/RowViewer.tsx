
import { Icons } from "../Icons";
import { CellRenderer } from "../CellRenderer";
import { TabButton, ViewModeButton } from "../components";
import { ColumnFilterPanel, ImportData, AutoCharts, ColumnVisibilityPanel } from "../";
import { isEncryptedValue } from "../utils";
import type { RowViewerProps } from "../types";
import type { ExportFormat } from "../types";

export function RowViewer({
  tableData,
  filteredRows,
  schemaColumns,
  columnStats,
  columnWidths,
  bookmarks,
  rowViewMode,
  editMode,
  editingRow,
  editValues,
  expandedCells,
  rowFilter,
  selectedRows,
  selectAll,
  showColumnStats,
  showExportMenu,
  copiedCell,
  page,
  pageSize,
  onSetView,
  onSetTableData,
  onSetRowViewMode,
  onSetRowFilter,
  onSetEditMode,
  onToggleCellExpand,
  onCopyToClipboard,
  onOpenTable,
  onStartEdit,
  onCancelEdit,
  onSaveEdit,
  onDeleteRow,
  onToggleRowSelection,
  onToggleSelectAll,
  onDeleteSelectedRows,
  onSetShowColumnStats,
  onExportData,
  onSetShowExportMenu,
  onStartResize,
  onAddBookmark,
  onRemoveBookmark,
  onSetEditValues,
  isBookmarked,
  canMutate,
  containerRef,
  columnFilters,
  onAddColumnFilter,
  onRemoveColumnFilter,
  onClearColumnFilters,
  onImportRows,
  visibleColumns,
  hiddenColumns,
  onToggleColumnVisibility,
  onShowAllColumns,
  onHideAllColumns,
}: RowViewerProps) {
  const totalPages = Math.ceil(tableData.total / pageSize);

  const columnTypes = schemaColumns.reduce((acc, col) => {
    acc[col.name] = col.type;
    return acc;
  }, {} as Record<string, string>);

  // Use visible columns instead of all columns
  const displayColumns = visibleColumns.length > 0 ? visibleColumns : tableData.columns;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={() => {
            onSetView("tables");
            onSetTableData(null);
          }}
          className="text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          ← Tables
        </button>
        <h2 className="font-mono text-sm font-medium">{tableData.table}</h2>

        <div className="flex gap-1">
          <TabButton
            active={false}
            onClick={() => {}}
          >
            Data
          </TabButton>
          <TabButton active={false} onClick={() => onSetView("schema")}>
            Schema
          </TabButton>
          <TabButton active={false} onClick={() => onSetView("relations")}>
            Relations
          </TabButton>
        </div>

        <span className="text-xs text-muted-foreground">
          {tableData.total.toLocaleString()} rows
        </span>

        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => {
              if (isBookmarked("table", tableData.table)) {
                const bm = bookmarks.find(
                  (b) => b.type === "table" && b.value === tableData.table
                );
                if (bm) onRemoveBookmark(bm.timestamp);
              } else {
                onAddBookmark("table", tableData.table, tableData.table);
              }
            }}
            className={`p-1.5 rounded ${
              isBookmarked("table", tableData.table)
                ? "text-primary"
                : "text-muted-foreground hover:text-primary"
            }`}
            title={
              isBookmarked("table", tableData.table)
                ? "Remove bookmark"
                : "Add bookmark"
            }
          >
            {isBookmarked("table", tableData.table) ? (
              <Icons.BookmarkSolid />
            ) : (
              <Icons.Bookmark />
            )}
          </button>
          <button
            onClick={() => {
              onSetView("query");
            }}
            className="text-xs text-primary hover:text-primary/80 transition-colors"
          >
            Query
          </button>
        </div>
      </div>

      {/* Controls */}
      <div className="flex items-center gap-3 flex-wrap p-3 bg-card border border-border rounded-none">
        {/* View Mode Toggle */}
        <div className="flex items-center gap-1">
          <ViewModeButton
            mode="list"
            current={rowViewMode}
            onClick={onSetRowViewMode}
            icon={<Icons.List />}
          />
          <ViewModeButton
            mode="cards"
            current={rowViewMode}
            onClick={onSetRowViewMode}
            icon={<Icons.Cards />}
          />
          <ViewModeButton
            mode="compact"
            current={rowViewMode}
            onClick={onSetRowViewMode}
            icon={<Icons.Grid />}
          />
        </div>

        <div className="w-px h-6 bg-border" />

        {/* Filter */}
        <div className="relative flex-1 max-w-xs">
          <div className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground/50">
            <Icons.Filter />
          </div>
          <input
            type="text"
            value={rowFilter}
            onChange={(e) => onSetRowFilter(e.target.value)}
            placeholder="Filter rows..."
            className="w-full bg-input border border-border rounded-none pl-7 pr-3 py-1.5 text-sm placeholder:text-muted-foreground/40 focus:outline-none focus:border-primary/40"
          />
          {rowFilter && (
            <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-muted-foreground/50">
              {filteredRows.length}
            </span>
          )}
        </div>

        <div className="w-px h-6 bg-border" />

        {/* Edit Toggle */}
        <label className="inline-flex items-center gap-2 select-none cursor-pointer">
          <input
            type="checkbox"
            checked={editMode}
            onChange={(e) => onSetEditMode(e.target.checked)}
            className="w-3.5 h-3.5 accent-primary cursor-pointer"
          />
          <span className="text-xs text-muted-foreground">Edit</span>
        </label>

        {editMode && (tableData?.primaryKeys?.length ?? 0) === 0 && (
          <span className="text-[11px] text-muted-foreground/60 italic">No PK</span>
        )}

        <div className="w-px h-6 bg-border" />

        {/* Stats Toggle */}
        <button
          onClick={() => onSetShowColumnStats(!showColumnStats)}
          className={`flex items-center gap-1 px-2 py-1 text-xs rounded ${
            showColumnStats
              ? "bg-primary/20 text-primary"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          <Icons.BarChart />
          Stats
        </button>

        <div className="w-px h-6 bg-border" />

        {/* Column Visibility */}
        <ColumnVisibilityPanel
          columns={tableData.columns}
          hiddenColumns={hiddenColumns}
          onToggle={onToggleColumnVisibility}
          onShowAll={onShowAllColumns}
          onHideAll={onHideAllColumns}
        />

        <div className="w-px h-6 bg-border" />

        {/* Column Filter */}
        <ColumnFilterPanel
          columns={tableData.columns}
          activeFilters={columnFilters}
          onAddFilter={onAddColumnFilter}
          onRemoveFilter={onRemoveColumnFilter}
          onClearFilters={onClearColumnFilters}
        />

        <div className="w-px h-6 bg-border" />

        {/* Import */}
        {canMutate && (
          <ImportData
            tableName={tableData.table}
            columns={tableData.columns}
            onImport={onImportRows}
          />
        )}

        <div className="w-px h-6 bg-border" />

        {/* Charts */}
        <AutoCharts columns={displayColumns} rows={filteredRows} />

        <div className="w-px h-6 bg-border" />

        {/* Export */}
        <div className="relative">
          <button
            onClick={() => onSetShowExportMenu(!showExportMenu)}
            className="flex items-center gap-1 px-2 py-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <Icons.Download />
            Export
          </button>
          {showExportMenu && (
            <div className="absolute right-0 top-full mt-1 w-32 bg-card border border-border rounded-none z-50">
              {(["csv", "json", "sql"] as ExportFormat[]).map((fmt) => (
                <button
                  key={fmt}
                  onClick={() => onExportData(fmt)}
                  className="w-full px-3 py-2 text-left text-xs hover:bg-input first:rounded-t-lg last:rounded-b-lg uppercase"
                >
                  {fmt}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Bulk actions when rows selected */}
        {selectedRows.size > 0 && canMutate && (
          <>
            <div className="w-px h-6 bg-border" />
            <span className="text-xs text-muted-foreground">
              {selectedRows.size} selected
            </span>
            <button
              onClick={onDeleteSelectedRows}
              className="px-2 py-1 text-xs bg-destructive/20 text-destructive rounded hover:bg-destructive/30 transition-colors"
            >
              Delete
            </button>
          </>
        )}
      </div>

      {/* Column Stats */}
      {showColumnStats && columnStats.length > 0 && (
        <div className="p-3 bg-card border border-border rounded-none">
          <h4 className="text-xs font-medium mb-3 flex items-center gap-2">
            <Icons.BarChart />
            Column Statistics
          </h4>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
            {columnStats.map((stat) => (
              <div
                key={stat.name}
                className="p-2 bg-input rounded border border-border/50"
              >
                <div
                  className="text-[10px] text-muted-foreground truncate"
                  title={stat.name}
                >
                  {stat.name}
                </div>
                <div className="text-[10px] mt-1 space-y-0.5">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground/60">Nulls:</span>
                    <span>{stat.nullCount}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground/60">Unique:</span>
                    <span>{stat.uniqueCount}</span>
                  </div>
                  {stat.avg !== undefined && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground/60">Avg:</span>
                      <span className="font-mono">{stat.avg}</span>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Encrypted Data Warning */}
      {tableData.rows.some((row) =>
        Object.values(row).some((v) => isEncryptedValue(v))
      ) && (
        <div className="px-3 py-2 bg-amber-500/10 border border-amber-500/30 rounded-none flex items-center gap-2 text-[11px] text-amber-500">
          <Icons.Warning />
          <span>
            Some fields are encrypted. The decryption key may not be available
            or the data uses a different encryption domain.
          </span>
        </div>
      )}

      {/* Data Display */}
      {rowViewMode === "list" && (
        <ListView
          columns={displayColumns}
          rows={filteredRows}
          columnTypes={columnTypes}
          columnWidths={columnWidths}
          editable={canMutate}
          showSelection={canMutate}
          editingRow={editingRow}
          editValues={editValues}
          selectedRows={selectedRows}
          selectAll={selectAll}
          expandedCells={expandedCells}
          copiedCell={copiedCell}
          onToggleRowSelection={onToggleRowSelection}
          onToggleSelectAll={onToggleSelectAll}
          onStartEdit={onStartEdit}
          onCancelEdit={onCancelEdit}
          onSaveEdit={onSaveEdit}
          onDeleteRow={onDeleteRow}
          onToggleCellExpand={onToggleCellExpand}
          onCopyToClipboard={onCopyToClipboard}
          onStartResize={onStartResize}
          onSetEditValues={onSetEditValues}
          containerRef={containerRef}
        />
      )}
      {rowViewMode === "cards" && (
        <CardsView
          columns={displayColumns}
          rows={filteredRows}
          editable={canMutate}
          editingRow={editingRow}
          editValues={editValues}
          expandedCells={expandedCells}
          copiedCell={copiedCell}
          onStartEdit={onStartEdit}
          onCancelEdit={onCancelEdit}
          onSaveEdit={onSaveEdit}
          onDeleteRow={onDeleteRow}
          onToggleCellExpand={onToggleCellExpand}
          onCopyToClipboard={onCopyToClipboard}
          onSetEditValues={onSetEditValues}
        />
      )}
      {rowViewMode === "compact" && (
        <CompactView columns={displayColumns} rows={filteredRows} />
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center gap-2 justify-center pt-2">
          <button
            disabled={page === 0}
            onClick={() => onOpenTable(tableData.table, page - 1)}
            className="px-3 py-1.5 text-xs rounded-none bg-card border border-border disabled:opacity-30 hover:bg-input transition-colors"
          >
            ← Prev
          </button>
          <span className="text-xs text-muted-foreground px-2">
            Page {page + 1} of {totalPages}
          </span>
          <button
            disabled={page >= totalPages - 1}
            onClick={() => onOpenTable(tableData.table, page + 1)}
            className="px-3 py-1.5 text-xs rounded-none bg-card border border-border disabled:opacity-30 hover:bg-input transition-colors"
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

interface ListViewProps {
  columns: string[];
  rows: Record<string, unknown>[];
  columnTypes: Record<string, string>;
  columnWidths: Record<string, number>;
  editable: boolean;
  showSelection: boolean;
  editingRow: number | null;
  editValues: Record<string, string>;
  selectedRows: Set<number>;
  selectAll: boolean;
  expandedCells: Set<string>;
  copiedCell: string | null;
  onToggleRowSelection: (index: number) => void;
  onToggleSelectAll: () => void;
  onStartEdit: (rowIndex: number, row: Record<string, unknown>) => void;
  onCancelEdit: () => void;
  onSaveEdit: (originalRow: Record<string, unknown>) => void;
  onDeleteRow: (row: Record<string, unknown>) => void;
  onToggleCellExpand: (rowIdx: number, col: string) => void;
  onCopyToClipboard: (text: string, identifier: string) => void;
  onStartResize: (e: React.MouseEvent, col: string) => void;
  onSetEditValues: (values: Record<string, string>) => void;
  containerRef: React.RefObject<HTMLDivElement | null>;
}

function ListView({
  columns,
  rows,
  columnTypes,
  columnWidths,
  editable,
  showSelection,
  editingRow,
  editValues,
  selectedRows,
  selectAll,
  expandedCells,
  copiedCell,
  onToggleRowSelection,
  onToggleSelectAll,
  onStartEdit,
  onCancelEdit,
  onSaveEdit,
  onDeleteRow,
  onToggleCellExpand,
  onCopyToClipboard,
  onStartResize,
  onSetEditValues,
  containerRef,
}: ListViewProps) {
  return (
    <div className="border border-border rounded-none overflow-hidden">
      <div className="overflow-auto max-h-[calc(100vh-400px)]" ref={containerRef}>
        <table className="w-full text-[12px] font-mono">
          <thead className="sticky top-0 z-10">
            <tr className="bg-card border-b border-border">
              {showSelection && (
                <th className="px-2 py-2 text-left w-[30px]">
                  <input
                    type="checkbox"
                    checked={selectAll}
                    onChange={onToggleSelectAll}
                    className="w-3.5 h-3.5 accent-primary cursor-pointer"
                  />
                </th>
              )}
              {editable && (
                <th className="px-2 py-2 text-left text-muted-foreground font-medium whitespace-nowrap w-[60px]">
                  Actions
                </th>
              )}
              {columns.map((col) => (
                <th
                  key={col}
                  className="px-3 py-2 text-left text-muted-foreground font-medium whitespace-nowrap relative group"
                  style={{ width: columnWidths[col] || 150, minWidth: 50 }}
                >
                  <div className="flex flex-col">
                    <span className="truncate">{col}</span>
                    {columnTypes[col] && (
                      <span className="text-[9px] text-muted-foreground/40 font-normal">
                        {columnTypes[col]}
                      </span>
                    )}
                  </div>
                  {/* Resize handle */}
                  <div
                    className="absolute right-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-primary/50 opacity-0 group-hover:opacity-100"
                    onMouseDown={(e) => onStartResize(e, col)}
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length + (editable ? 1 : 0) + (showSelection ? 1 : 0)}
                  className="px-3 py-8 text-center text-muted-foreground/50"
                >
                  No rows
                </td>
              </tr>
            ) : (
              rows.map((row, i) => {
                const isEditing = editable && editingRow === i;
                const rowKey = `${i}`;
                return (
                  <tr
                    key={rowKey}
                    className="border-b border-border/50 hover:bg-card/40 transition-colors"
                  >
                    {showSelection && (
                      <td className="px-2 py-1.5">
                        <input
                          type="checkbox"
                          checked={selectedRows.has(i)}
                          onChange={() => onToggleRowSelection(i)}
                          className="w-3.5 h-3.5 accent-primary cursor-pointer"
                        />
                      </td>
                    )}
                    {editable && (
                      <td className="px-2 py-1.5">
                        {isEditing ? (
                          <div className="flex gap-1">
                            <button
                              onClick={() => onSaveEdit(row)}
                              className="text-[10px] text-primary hover:underline"
                            >
                              Save
                            </button>
                            <button
                              onClick={onCancelEdit}
                              className="text-[10px] text-muted-foreground hover:underline"
                            >
                              Cancel
                            </button>
                          </div>
                        ) : (
                          <div className="flex gap-1 opacity-0 hover:opacity-100 transition-opacity">
                            <button
                              onClick={() => onStartEdit(i, row)}
                              className="p-1 text-muted-foreground hover:text-primary"
                            >
                              <Icons.Edit />
                            </button>
                            <button
                              onClick={() => onDeleteRow(row)}
                              className="p-1 text-muted-foreground hover:text-destructive"
                            >
                              <Icons.Trash />
                            </button>
                          </div>
                        )}
                      </td>
                    )}
                    {columns.map((col) => {
                      const cellKey = `${i}-${col}`;
                      const value = row[col];
                      return (
                        <td
                          key={col}
                          className="px-3 py-1.5 whitespace-nowrap max-w-[300px] truncate"
                          style={{ width: columnWidths[col] || 150 }}
                        >
                          <CellRenderer
                            value={value}
                            rowIdx={i}
                            col={col}
                            isEditing={isEditing}
                            editValue={editValues[col]}
                            onEditChange={(v) =>
                              onSetEditValues({ ...editValues, [col]: v })
                            }
                            isExpanded={expandedCells.has(cellKey)}
                            onToggleExpand={() => onToggleCellExpand(i, col)}
                            onCopy={() => onCopyToClipboard(String(value ?? ""), cellKey)}
                            copied={copiedCell === cellKey}
                          />
                        </td>
                      );
                    })}
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

interface CardsViewProps {
  columns: string[];
  rows: Record<string, unknown>[];
  editable: boolean;
  editingRow: number | null;
  editValues: Record<string, string>;
  expandedCells: Set<string>;
  copiedCell: string | null;
  onStartEdit: (rowIndex: number, row: Record<string, unknown>) => void;
  onCancelEdit: () => void;
  onSaveEdit: (originalRow: Record<string, unknown>) => void;
  onDeleteRow: (row: Record<string, unknown>) => void;
  onToggleCellExpand: (rowIdx: number, col: string) => void;
  onCopyToClipboard: (text: string, identifier: string) => void;
  onSetEditValues: (values: Record<string, string>) => void;
}

function CardsView({
  columns,
  rows,
  editable,
  editingRow,
  editValues,
  expandedCells,
  copiedCell,
  onStartEdit,
  onCancelEdit,
  onSaveEdit,
  onDeleteRow,
  onToggleCellExpand,
  onCopyToClipboard,
  onSetEditValues,
}: CardsViewProps) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
      {rows.length === 0 ? (
        <div className="col-span-full py-8 text-center text-muted-foreground/50">
          No rows
        </div>
      ) : (
        rows.map((row, i) => {
          const isEditing = editable && editingRow === i;
          const displayCols = columns.slice(0, 6);
          const remainingCols = columns.slice(6);

          return (
            <div
              key={i}
              className="bg-card border border-border rounded-none p-3 hover:border-primary/30 transition-colors"
            >
              {/* Card Header */}
              <div className="flex items-center justify-between mb-2 pb-2 border-b border-border/50">
                <span className="text-[10px] text-muted-foreground">Row {i + 1}</span>
                <div className="flex gap-1">
                  {editable && (
                    <>
                      {isEditing ? (
                        <>
                          <button
                            onClick={() => onSaveEdit(row)}
                            className="text-[10px] px-2 py-0.5 bg-primary/20 text-primary rounded"
                          >
                            Save
                          </button>
                          <button
                            onClick={onCancelEdit}
                            className="text-[10px] px-2 py-0.5 bg-input text-muted-foreground rounded"
                          >
                            Cancel
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            onClick={() => onStartEdit(i, row)}
                            className="p-1 text-muted-foreground hover:text-primary"
                          >
                            <Icons.Edit />
                          </button>
                          <button
                            onClick={() => onDeleteRow(row)}
                            className="p-1 text-muted-foreground hover:text-destructive"
                          >
                            <Icons.Trash />
                          </button>
                        </>
                      )}
                    </>
                  )}
                  <button
                    onClick={() =>
                      onCopyToClipboard(JSON.stringify(row), `card-${i}`)
                    }
                    className="p-1 text-muted-foreground/50 hover:text-primary"
                    title="Copy row as JSON"
                  >
                    {copiedCell === `card-${i}` ? <Icons.Check /> : <Icons.Copy />}
                  </button>
                </div>
              </div>

              {/* Card Body */}
              <div className="space-y-1.5">
                {displayCols.map((col) => (
                  <div key={col} className="flex items-start gap-2">
                    <span
                      className="text-[10px] text-muted-foreground/60 w-20 shrink-0 truncate"
                      title={col}
                    >
                      {col}
                    </span>
                    <div className="flex-1 min-w-0 text-[11px] truncate">
                      {isEditing ? (
                        <input
                          type="text"
                          value={editValues[col] ?? ""}
                          onChange={(e) =>
                            onSetEditValues({
                              ...editValues,
                              [col]: e.target.value,
                            })
                          }
                          className="w-full bg-input border border-border rounded px-1.5 py-0.5 text-[11px] outline-none"
                        />
                      ) : (
                        <CellRenderer
                          value={row[col]}
                          rowIdx={i}
                          col={col}
                          isExpanded={expandedCells.has(`${i}-${col}`)}
                          onToggleExpand={() => onToggleCellExpand(i, col)}
                          onCopy={() =>
                            onCopyToClipboard(
                              String(row[col] ?? ""),
                              `card-${i}-${col}`
                            )
                          }
                          copied={copiedCell === `card-${i}-${col}`}
                        />
                      )}
                    </div>
                  </div>
                ))}
                {remainingCols.length > 0 && (
                  <div className="text-[10px] text-muted-foreground/40 pt-1">
                    +{remainingCols.length} more columns
                  </div>
                )}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

interface CompactViewProps {
  columns: string[];
  rows: Record<string, unknown>[];
}

function CompactView({ columns, rows }: CompactViewProps) {
  return (
    <div className="border border-border rounded-none overflow-hidden">
      <div className="overflow-auto max-h-[calc(100vh-400px)]">
        <table className="w-full text-[11px]">
          <thead className="sticky top-0 bg-card border-b border-border">
            <tr>
              {columns.slice(0, 5).map((col) => (
                <th
                  key={col}
                  className="px-2 py-1.5 text-left text-muted-foreground font-medium"
                >
                  {col}
                </th>
              ))}
              {columns.length > 5 && (
                <th className="px-2 py-1.5 text-muted-foreground">…</th>
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-border/30 hover:bg-card/30">
                {columns.slice(0, 5).map((col) => (
                  <td key={col} className="px-2 py-1 truncate max-w-[150px]">
                    <CompactCell value={row[col]} />
                  </td>
                ))}
                {columns.length > 5 && (
                  <td className="px-2 py-1 text-muted-foreground/40">
                    +{columns.length - 5}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CompactCell({ value }: { value: unknown }) {
  if (isEncryptedValue(value)) {
    return (
      <span className="inline-flex items-center gap-1 text-[9px] px-1 rounded bg-amber-500/15 text-amber-500">
        <Icons.Lock />
      </span>
    );
  }
  if (value === null) return <span className="text-muted-foreground/30">∅</span>;
  if (value === "") return <span className="text-muted-foreground/20">""</span>;
  const str = String(value);
  if (str.length > 30) return str.slice(0, 30) + "…";
  return str;
}
