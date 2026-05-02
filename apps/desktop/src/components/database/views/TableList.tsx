import { Icons } from "../Icons";
import { EmptyState } from "../EmptyState";
import type { TableListProps } from "../types";

export function TableList({
  tables,
  filteredTables,
  tableSearch,
  bookmarks,
  onOpenTable,
  onSetTableSearch,
  onLoadTables,
  onSetSql,
  onSetView,
  onAddBookmark,
  onRemoveBookmark,
  isBookmarked,
}: TableListProps) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-sm">
          <div className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground/50">
            <Icons.Search />
          </div>
          <input
            type="text"
            value={tableSearch}
            onChange={(e) => onSetTableSearch(e.target.value)}
            placeholder="Search tables..."
            className="w-full bg-input border border-border rounded-none pl-9 pr-3 py-2 text-sm placeholder:text-muted-foreground/40 focus:outline-none focus:border-primary/40"
          />
          {tableSearch && (
            <button
              onClick={() => onSetTableSearch("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground/50 hover:text-foreground"
            >
              ×
            </button>
          )}
        </div>
        <button
          onClick={onLoadTables}
          className="p-2 text-muted-foreground hover:text-foreground transition-colors"
          title="Refresh"
        >
          <Icons.Refresh />
        </button>
      </div>

      <div className="text-sm text-muted-foreground">
        {filteredTables.length} of {tables.length} tables
      </div>

      {filteredTables.length === 0 ? (
        tableSearch ? (
          <EmptyState
            type="no-results"
            onAction={() => onSetTableSearch("")}
            actionLabel="Clear search"
          />
        ) : (
          <EmptyState
            type="no-tables"
            onAction={() => {}}
            actionLabel="Create table"
          />
        )
      ) : (
        <div className="grid gap-2">
          {filteredTables.map((t) => (
            <div
              key={t.name}
              onClick={() => onOpenTable(t.name)}
              className="flex items-center justify-between p-3 rounded-none bg-card/50 hover:bg-card border border-border/50 hover:border-border transition-all cursor-pointer group"
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded bg-primary/10 flex items-center justify-center text-primary">
                  <Icons.Table />
                </div>
                <div>
                  <div className="font-mono text-sm group-hover:text-primary transition-colors">
                    {t.name}
                  </div>
                  <div className="text-[11px] text-muted-foreground">
                    {t.rowCount.toLocaleString()} rows
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    if (isBookmarked("table", t.name)) {
                      const bm = bookmarks.find(
                        (b) => b.type === "table" && b.value === t.name
                      );
                      if (bm) onRemoveBookmark(bm.timestamp);
                    } else {
                      onAddBookmark("table", t.name, t.name);
                    }
                  }}
                  className={`p-1.5 rounded ${
                    isBookmarked("table", t.name)
                      ? "text-primary"
                      : "text-muted-foreground hover:text-primary"
                  }`}
                  title={
                    isBookmarked("table", t.name)
                      ? "Remove bookmark"
                      : "Add bookmark"
                  }
                >
                  {isBookmarked("table", t.name) ? (
                    <Icons.BookmarkSolid />
                  ) : (
                    <Icons.Bookmark />
                  )}
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onSetSql(`SELECT * FROM "${t.name}" LIMIT 100`);
                    onSetView("query");
                  }}
                  className="px-2 py-1 text-[10px] bg-input border border-border rounded hover:border-primary/50"
                >
                  Query
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
