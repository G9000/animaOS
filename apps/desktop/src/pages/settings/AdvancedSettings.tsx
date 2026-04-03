import { useState } from "react";
import { getDbViewerEnabled, setDbViewerEnabled } from "../../lib/preferences";

export default function AdvancedSettings() {
  const [dbViewer, setDbViewer] = useState(getDbViewerEnabled);

  return (
    <div className="space-y-6">
      <section className="rounded-sm border border-border bg-card p-5 space-y-5">
        <header className="space-y-1">
          <h2 className="text-[11px] text-muted-foreground uppercase tracking-wider">
            Debug
          </h2>
          <p className="text-xs text-muted-foreground">
            Advanced tools for inspecting application internals. Use with
            caution.
          </p>
        </header>

        <label className="flex items-center justify-between gap-4 cursor-pointer group">
          <div>
            <p className="text-sm text-foreground group-hover:text-primary transition-colors">
              Database Viewer
            </p>
            <p className="text-xs text-muted-foreground">
              Show the DB inspector in the sidebar. Lets you browse tables, run
              queries, and edit or delete rows.
            </p>
          </div>
          <input
            type="checkbox"
            checked={dbViewer}
            onChange={(e) => {
              const enabled = e.target.checked;
              setDbViewer(enabled);
              setDbViewerEnabled(enabled);
            }}
            className="w-4 h-4 accent-primary cursor-pointer shrink-0"
          />
        </label>
      </section>
    </div>
  );
}
