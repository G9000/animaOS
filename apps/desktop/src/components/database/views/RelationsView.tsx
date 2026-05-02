import { Icons } from "../Icons";
import type { RelationsViewProps } from "../types";

export function RelationsView({
  tableData,
  tables,
  foreignKeys,
  onSetView,
  onOpenTable,
}: RelationsViewProps) {
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <button
          onClick={() => onSetView("rows")}
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← Back
        </button>
        <h2 className="font-mono text-sm font-medium">
          {tableData.table} — Relations
        </h2>
      </div>

      {/* Foreign Keys From This Table */}
      <div>
        <h3 className="text-xs font-medium mb-3 text-muted-foreground uppercase tracking-wide">
          Foreign Keys
        </h3>
        {foreignKeys.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground/50 bg-card border border-border rounded-none">
            <Icons.Network />
            <p className="mt-2">No foreign key relations detected</p>
            <p className="text-xs mt-1">
              Column names ending in _id will be shown here
            </p>
          </div>
        ) : (
          <div className="grid gap-3">
            {foreignKeys.map((fk, i) => (
              <div
                key={i}
                className="p-4 bg-card border border-border rounded-none"
              >
                <div className="flex items-center gap-3 text-sm">
                  <span className="font-mono text-muted-foreground">
                    {fk.fromTable}
                  </span>
                  <span className="font-mono">{fk.fromColumn}</span>
                  <Icons.Link />
                  <button
                    onClick={() => onOpenTable(fk.toTable)}
                    className="font-mono text-primary hover:underline"
                  >
                    {fk.toTable}
                  </button>
                  <span className="font-mono text-muted-foreground">
                    {fk.toColumn}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Tables That Might Reference This */}
      <div>
        <h3 className="text-xs font-medium mb-3 text-muted-foreground uppercase tracking-wide">
          Potentially Related Tables
        </h3>
        <div className="grid gap-2">
          {tables
            .filter((t) => t.name !== tableData.table)
            .slice(0, 10)
            .map((t) => (
              <button
                key={t.name}
                onClick={() => onOpenTable(t.name)}
                className="flex items-center justify-between p-3 bg-card border border-border rounded-none hover:border-primary/30 transition-colors"
              >
                <span className="font-mono text-sm">{t.name}</span>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">
                    {t.rowCount.toLocaleString()} rows
                  </span>
                  <Icons.Link />
                </div>
              </button>
            ))}
        </div>
      </div>
    </div>
  );
}
