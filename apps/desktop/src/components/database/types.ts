import type { DbTableInfo, DbTableData, DbQueryResult } from "@anima/api-client";

export type View = "tables" | "rows" | "query" | "schema" | "dashboard" | "relations";
export type RowViewMode = "list" | "cards" | "compact";
export type ExportFormat = "csv" | "json" | "sql";

export interface QueryHistoryItem {
  sql: string;
  timestamp: number;
  rowCount?: number;
}

export interface ColumnInfo {
  name: string;
  type: string;
  nullable: boolean;
  default: string | null;
  primaryKey: boolean;
}

export interface TableStats {
  totalRows: number;
  totalTables: number;
  largestTable: string;
  recentQueries: number;
}

export interface TableRelation {
  fromTable: string;
  fromColumn: string;
  toTable: string;
  toColumn: string;
}

export interface Bookmark {
  type: "table" | "query";
  name: string;
  value: string;
  timestamp: number;
}

export interface DashboardProps {
  tables: DbTableInfo[];
  stats: TableStats | null;
  recentTables: string[];
  bookmarks: Bookmark[];
  topTables: DbTableInfo[];
  onOpenTable: (name: string) => void;
  onSetView: (view: View) => void;
  onRemoveBookmark: (timestamp: number) => void;
  isBookmarked: (type: "table" | "query", value: string) => boolean;
}

export interface TableListProps {
  tables: DbTableInfo[];
  filteredTables: DbTableInfo[];
  tableSearch: string;
  bookmarks: Bookmark[];
  onOpenTable: (name: string) => void;
  onSetTableSearch: (value: string) => void;
  onLoadTables: () => void;
  onSetSql: (sql: string) => void;
  onSetView: (view: View) => void;
  onAddBookmark: (type: "table" | "query", name: string, value: string) => void;
  onRemoveBookmark: (timestamp: number) => void;
  isBookmarked: (type: "table" | "query", value: string) => boolean;
}

export interface RowViewerProps {
  tableData: DbTableData;
  filteredRows: Record<string, unknown>[];
  schemaColumns: ColumnInfo[];
  columnStats: import("./hooks/useColumnStats").ColumnStats[];
  columnWidths: Record<string, number>;
  bookmarks: Bookmark[];
  rowViewMode: RowViewMode;
  editMode: boolean;
  editingRow: number | null;
  editValues: Record<string, string>;
  expandedCells: Set<string>;
  rowFilter: string;
  selectedRows: Set<number>;
  selectAll: boolean;
  showColumnStats: boolean;
  showExportMenu: boolean;
  copiedCell: string | null;
  page: number;
  pageSize: number;
  // Column filters
  columnFilters: import("./ColumnFilter").ColumnFilter[];
  onAddColumnFilter: (filter: import("./ColumnFilter").ColumnFilter) => void;
  onRemoveColumnFilter: (index: number) => void;
  onClearColumnFilters: () => void;
  // Import
  onImportRows: (rows: Record<string, unknown>[]) => Promise<void>;
  // Other handlers
  onSetView: (view: View) => void;
  onSetTableData: (data: DbTableData | null) => void;
  onSetRowViewMode: (mode: RowViewMode) => void;
  onSetRowFilter: (value: string) => void;
  onSetEditMode: (value: boolean) => void;
  onToggleCellExpand: (rowIdx: number, col: string) => void;
  onCopyToClipboard: (text: string, identifier: string) => void;
  onOpenTable: (name: string, page?: number) => void;
  onStartEdit: (rowIndex: number, row: Record<string, unknown>) => void;
  onCancelEdit: () => void;
  onSaveEdit: (originalRow: Record<string, unknown>) => void;
  onDeleteRow: (row: Record<string, unknown>) => void;
  onToggleRowSelection: (index: number) => void;
  onToggleSelectAll: () => void;
  onDeleteSelectedRows: () => void;
  onSetShowColumnStats: (value: boolean) => void;
  onExportData: (format: ExportFormat) => void;
  onSetShowExportMenu: (value: boolean) => void;
  onStartResize: (e: React.MouseEvent, col: string) => void;
  onAddBookmark: (type: "table" | "query", name: string, value: string) => void;
  onRemoveBookmark: (timestamp: number) => void;
  onSetEditValues: (values: Record<string, string>) => void;
  isBookmarked: (type: "table" | "query", value: string) => boolean;
  canMutate: boolean;
  containerRef: React.RefObject<HTMLDivElement | null>;
  // Column visibility
  visibleColumns: string[];
  hiddenColumns: string[];
  onToggleColumnVisibility: (column: string) => void;
  onShowAllColumns: () => void;
  onHideAllColumns: () => void;
}

export interface SchemaViewProps {
  tableData: DbTableData;
  schemaColumns: ColumnInfo[];
  schemaIndexes: string[];
  onSetView: (view: View) => void;
}

export interface RelationsViewProps {
  tableData: DbTableData;
  tables: DbTableInfo[];
  foreignKeys: TableRelation[];
  onSetView: (view: View) => void;
  onOpenTable: (name: string) => void;
}

export interface QueryEditorProps {
  sql: string;
  queryResult: DbQueryResult | null;
  queryHistory: QueryHistoryItem[];
  bookmarks: Bookmark[];
  showHistory: boolean;
  loading: boolean;
  onSetSql: (sql: string) => void;
  onRunQuery: () => void;
  onSetShowHistory: (value: boolean) => void;
  onSetQueryHistory: (history: QueryHistoryItem[]) => void;
  onSetView: (view: View) => void;
  onAddBookmark: (type: "table" | "query", name: string, value: string) => void;
  onRemoveBookmark: (timestamp: number) => void;
  isBookmarked: (type: "table" | "query", value: string) => boolean;
}
