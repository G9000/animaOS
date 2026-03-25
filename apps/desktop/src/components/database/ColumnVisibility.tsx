import { Icons } from "./Icons";

interface ColumnVisibilityProps {
  columns: string[];
  hiddenColumns: string[];
  onToggle: (column: string) => void;
  onShowAll: () => void;
  onHideAll: () => void;
}

export function ColumnVisibilityPanel({
  columns,
  hiddenColumns,
  onToggle,
  onShowAll,
  onHideAll,
}: ColumnVisibilityProps) {
  const visibleCount = columns.length - hiddenColumns.length;

  return (
    <div className="relative group">
      <button className="flex items-center gap-1 px-2 py-1 text-xs text-muted-foreground hover:text-foreground transition-colors">
        <Icons.Eye />
        Columns ({visibleCount}/{columns.length})
      </button>

      <div className="absolute right-0 top-full mt-1 w-48 bg-card border border-border rounded-lg shadow-lg z-50 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all">
        <div className="px-3 py-2 border-b border-border flex items-center justify-between">
          <span className="text-[11px] font-medium">Toggle Columns</span>
          <div className="flex gap-1">
            <button
              onClick={onShowAll}
              className="text-[10px] text-primary hover:underline"
            >
              All
            </button>
            <span className="text-muted-foreground">|</span>
            <button
              onClick={onHideAll}
              className="text-[10px] text-primary hover:underline"
            >
              None
            </button>
          </div>
        </div>
        <div className="max-h-60 overflow-y-auto py-1">
          {columns.map((col) => {
            const isHidden = hiddenColumns.includes(col);
            return (
              <label
                key={col}
                className="flex items-center gap-2 px-3 py-1.5 hover:bg-input cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={!isHidden}
                  onChange={() => onToggle(col)}
                  className="w-3.5 h-3.5 accent-primary"
                />
                <span className={`text-xs ${isHidden ? "text-muted-foreground" : ""}`}>
                  {col}
                </span>
              </label>
            );
          })}
        </div>
      </div>
    </div>
  );
}
