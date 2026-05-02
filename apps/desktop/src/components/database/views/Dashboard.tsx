import { Icons } from "../Icons";
import { StatCard } from "../components";
import { ERDiagram } from "../ERDiagram";
import type { DashboardProps } from "../types";

export function Dashboard({
  tables,
  stats,
  recentTables,
  bookmarks,
  topTables,
  onOpenTable,
  onSetView,
  onRemoveBookmark,
  isBookmarked,
}: DashboardProps) {
  return (
    <div className="space-y-6">
      {/* Stats Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          title="Total Tables"
          value={stats?.totalTables ?? "—"}
          icon={<Icons.Table />}
          color="border-blue-500/30"
        />
        <StatCard
          title="Total Rows"
          value={stats?.totalRows?.toLocaleString() ?? "—"}
          icon={<Icons.Grid />}
          color="border-green-500/30"
        />
        <StatCard
          title="Largest Table"
          value={stats?.largestTable ?? "—"}
          icon={<Icons.Table />}
          color="border-purple-500/30"
          subtitle={
            tables.find((t) => t.name === stats?.largestTable)?.rowCount.toLocaleString() +
            " rows"
          }
        />
        <StatCard
          title="Queries Run"
          value={stats?.recentQueries ?? 0}
          icon={<Icons.History />}
          color="border-orange-500/30"
        />
      </div>

      <div className="grid md:grid-cols-2 gap-6">
        {/* Top Tables */}
        <div className="bg-card border border-border rounded-none p-4">
          <h3 className="text-sm font-medium mb-4 flex items-center gap-2">
            <Icons.Table />
            Largest Tables
          </h3>
          <div className="space-y-2">
            {topTables.map((table, i) => (
              <div
                key={table.name}
                onClick={() => onOpenTable(table.name)}
                className="flex items-center justify-between p-2 rounded hover:bg-input cursor-pointer transition-colors group"
              >
                <div className="flex items-center gap-3">
                  <span className="text-xs text-muted-foreground w-5">#{i + 1}</span>
                  <span className="font-mono text-sm group-hover:text-primary transition-colors">
                    {table.name}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <div className="w-24 h-1.5 bg-input rounded-none overflow-hidden">
                    <div
                      className="h-full bg-primary/50 rounded-none"
                      style={{
                        width: `${Math.min(
                          100,
                          (table.rowCount / (topTables[0]?.rowCount || 1)) * 100
                        )}%`,
                      }}
                    />
                  </div>
                  <span className="text-xs text-muted-foreground w-16 text-right">
                    {table.rowCount.toLocaleString()}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Recent Tables */}
        <div className="bg-card border border-border rounded-none p-4">
          <h3 className="text-sm font-medium mb-4 flex items-center gap-2">
            <Icons.History />
            Recent Tables
          </h3>
          {recentTables.length === 0 ? (
            <p className="text-sm text-muted-foreground/50 italic">No recent tables</p>
          ) : (
            <div className="space-y-1">
              {recentTables.slice(0, 8).map((name) => (
                <button
                  key={name}
                  onClick={() => onOpenTable(name)}
                  className="w-full text-left px-2 py-1.5 rounded hover:bg-input text-sm font-mono transition-colors"
                >
                  {name}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Quick Actions */}
      <div className="bg-card border border-border rounded-none p-4">
        <h3 className="text-sm font-medium mb-4 flex items-center gap-2">
          <Icons.Eye />
          Quick Actions
        </h3>
        <div className="flex flex-wrap gap-2">
          <ERDiagram tables={tables} onOpenTable={onOpenTable} />
        </div>
      </div>

      {/* Bookmarks */}
      {bookmarks.length > 0 && (
        <div className="bg-card border border-border rounded-none p-4">
          <h3 className="text-sm font-medium mb-4 flex items-center gap-2">
            <Icons.Bookmark />
            Bookmarks
          </h3>
          <div className="flex flex-wrap gap-2">
            {bookmarks.slice(0, 10).map((bm) => (
              <button
                key={bm.timestamp}
                onClick={() => {
                  if (bm.type === "table") onOpenTable(bm.value);
                  else {
                    onSetView("query");
                    // SQL will be set by parent
                  }
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-input border border-border rounded-none hover:border-primary/50 transition-colors text-sm"
              >
                {bm.type === "table" ? <Icons.Table /> : <Icons.Eye />}
                <span className="font-mono">{bm.name}</span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onRemoveBookmark(bm.timestamp);
                  }}
                  className="ml-1 text-muted-foreground/50 hover:text-destructive"
                >
                  ×
                </button>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* All Tables Preview */}
      <div className="bg-card border border-border rounded-none p-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-medium flex items-center gap-2">
            <Icons.Grid />
            All Tables
          </h3>
          <button
            onClick={() => onSetView("tables")}
            className="text-xs text-primary hover:text-primary/80"
          >
            View All →
          </button>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          {tables.slice(0, 12).map((table) => (
            <button
              key={table.name}
              onClick={() => onOpenTable(table.name)}
              className="p-3 rounded-none bg-input hover:bg-input/80 border border-border hover:border-primary/30 transition-all text-left group"
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-mono text-xs truncate">{table.name}</span>
                {isBookmarked("table", table.name) && <Icons.BookmarkSolid />}
              </div>
              <div className="text-[10px] text-muted-foreground">
                {table.rowCount.toLocaleString()} rows
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
