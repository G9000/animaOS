import { useRef, useCallback } from "react";
import { Icons } from "../Icons";
import { QueryFavorites } from "../QueryFavorites";
import { convertToCsv, downloadFile, isEncryptedValue } from "../utils";
import type { QueryEditorProps } from "../types";

export function QueryEditor({
  sql,
  queryResult,
  queryHistory,
  bookmarks,
  showHistory,
  loading,
  onSetSql,
  onRunQuery,
  onSetShowHistory,
  onSetQueryHistory,
  onSetView,
  onAddBookmark,
  onRemoveBookmark,
  isBookmarked,
}: QueryEditorProps) {
  const queryRef = useRef<HTMLTextAreaElement>(null);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        onRunQuery();
      }
    },
    [onRunQuery]
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button
          onClick={() => onSetView("tables")}
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← Tables
        </button>
        <h2 className="text-sm font-medium">SQL Query</h2>
      </div>

      <div className="bg-card border border-border rounded-lg p-4">
        <textarea
          ref={queryRef}
          value={sql}
          onChange={(e) => onSetSql(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="SELECT * FROM users LIMIT 10"
          rows={5}
          className="w-full px-3 py-2.5 rounded-md bg-input border border-border text-[13px] font-mono resize-y placeholder:text-muted-foreground/40 focus:outline-none focus:border-primary/40 transition-colors"
        />
        <div className="flex items-center justify-between mt-3">
          <div className="flex items-center gap-3">
            <span className="text-[11px] text-muted-foreground/50">
              Ctrl+Enter to run
            </span>
            <QueryFavorites onSelectQuery={onSetSql} currentSql={sql} />
            <button
              onClick={() => onSetShowHistory(!showHistory)}
              className="text-[11px] text-muted-foreground hover:text-foreground flex items-center gap-1"
            >
              <Icons.History /> {showHistory ? "Hide" : "Show"} history (
              {queryHistory.length})
            </button>
            {sql && (
              <button
                onClick={() => {
                  if (isBookmarked("query", sql)) {
                    const bm = bookmarks.find(
                      (b) => b.type === "query" && b.value === sql
                    );
                    if (bm) onRemoveBookmark(bm.timestamp);
                  } else {
                    onAddBookmark(
                      "query",
                      sql.slice(0, 30) + (sql.length > 30 ? "…" : ""),
                      sql
                    );
                  }
                }}
                className={`text-[11px] flex items-center gap-1 ${
                  isBookmarked("query", sql)
                    ? "text-primary"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {isBookmarked("query", sql) ? (
                  <Icons.BookmarkSolid />
                ) : (
                  <Icons.Bookmark />
                )}
                {isBookmarked("query", sql) ? "Bookmarked" : "Bookmark"}
              </button>
            )}
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => onSetSql("")}
              className="px-3 py-1.5 text-xs rounded-md text-muted-foreground hover:text-foreground border border-transparent hover:border-border transition-colors"
            >
              Clear
            </button>
            <button
              onClick={onRunQuery}
              disabled={loading || !sql.trim()}
              className="px-4 py-1.5 text-xs rounded-md bg-primary text-white hover:bg-primary/90 disabled:opacity-30 transition-colors flex items-center gap-2"
            >
              {loading ? "Running…" : "Run Query"}
            </button>
          </div>
        </div>
      </div>

      {/* Query History */}
      {showHistory && queryHistory.length > 0 && (
        <div className="bg-card border border-border rounded-lg overflow-hidden">
          <div className="px-3 py-2 bg-input border-b border-border text-[11px] text-muted-foreground flex items-center justify-between">
            <span>Recent Queries</span>
            <button
              onClick={() => onSetQueryHistory([])}
              className="text-muted-foreground/50 hover:text-destructive"
            >
              Clear all
            </button>
          </div>
          <div className="max-h-40 overflow-y-auto">
            {queryHistory.map((item, i) => (
              <div
                key={i}
                className="flex items-center gap-2 px-3 py-2 hover:bg-input border-b border-border/50 last:border-0 group"
              >
                <button
                  onClick={() => {
                    onSetSql(item.sql);
                    onSetShowHistory(false);
                  }}
                  className="flex-1 text-left text-[11px] font-mono truncate"
                >
                  <span className="text-muted-foreground/40 w-12 inline-block">
                    {item.rowCount ?? "?"} rows
                  </span>
                  {item.sql}
                </button>
                <button
                  onClick={() =>
                    onAddBookmark("query", item.sql.slice(0, 30), item.sql)
                  }
                  className="opacity-0 group-hover:opacity-100 p-1 text-muted-foreground/50 hover:text-primary"
                  title="Bookmark"
                >
                  <Icons.Bookmark />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Results */}
      {queryResult && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">
              {queryResult.rowCount.toLocaleString()} rows returned
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => {
                  const csv = convertToCsv(queryResult);
                  downloadFile(csv, "query-result.csv", "text/csv");
                }}
                className="text-[11px] text-muted-foreground hover:text-primary flex items-center gap-1 transition-colors"
              >
                <Icons.Download /> CSV
              </button>
              <button
                onClick={() =>
                  downloadFile(
                    JSON.stringify(queryResult.rows, null, 2),
                    "query-result.json",
                    "application/json"
                  )
                }
                className="text-[11px] text-muted-foreground hover:text-primary flex items-center gap-1 transition-colors"
              >
                <Icons.Download /> JSON
              </button>
            </div>
          </div>

          {/* Encrypted Data Warning */}
          {queryResult.rows.some((row) =>
            Object.values(row).some((v) => isEncryptedValue(v))
          ) && (
            <div className="px-3 py-2 bg-amber-500/10 border border-amber-500/30 rounded-lg flex items-center gap-2 text-[11px] text-amber-500">
              <Icons.Warning />
              <span>Some fields are encrypted and cannot be displayed.</span>
            </div>
          )}

          <div className="border border-border rounded-lg overflow-hidden">
            <div className="overflow-auto max-h-[400px]">
              <table className="w-full text-[11px] font-mono">
                <thead className="sticky top-0 bg-card border-b border-border">
                  <tr>
                    {queryResult.columns.map((col) => (
                      <th
                        key={col}
                        className="px-3 py-2 text-left text-muted-foreground font-medium whitespace-nowrap"
                      >
                        {col}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {queryResult.rows.map((row, i) => (
                    <tr
                      key={i}
                      className="border-b border-border/30 hover:bg-card/30"
                    >
                      {queryResult.columns.map((col) => (
                        <td
                          key={col}
                          className="px-3 py-1.5 whitespace-nowrap max-w-[200px] truncate"
                        >
                          <QueryCell value={row[col]} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function QueryCell({ value }: { value: unknown }) {
  if (isEncryptedValue(value)) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500 border border-amber-500/30">
        <Icons.Lock />
        encrypted
      </span>
    );
  }
  if (value === null) return <span className="text-muted-foreground/40">NULL</span>;
  const str = String(value);
  if (str.length > 50) return str.slice(0, 50) + "…";
  return str;
}
