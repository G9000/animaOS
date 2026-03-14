import { useState, useEffect } from "react";

const DB_VIEWER_KEY = "anima-debug-db-viewer";

export function getDbViewerEnabled(): boolean {
  return localStorage.getItem(DB_VIEWER_KEY) === "true";
}

export default function AdvancedSettings() {
  const [dbViewer, setDbViewer] = useState(getDbViewerEnabled);

  useEffect(() => {
    localStorage.setItem(DB_VIEWER_KEY, String(dbViewer));
    // Notify other components (e.g. Layout sidebar) about the change
    window.dispatchEvent(new Event("anima-settings-changed"));
  }, [dbViewer]);

  return (
    <div className="space-y-6">
      <section className="rounded-sm border border-(--color-border) bg-(--color-bg-card) p-5 space-y-5">
        <header className="space-y-1">
          <h2 className="text-[11px] text-(--color-text-muted) uppercase tracking-wider">
            Debug
          </h2>
          <p className="text-xs text-(--color-text-muted)">
            Advanced tools for inspecting application internals. Use with
            caution.
          </p>
        </header>

        <label className="flex items-center justify-between gap-4 cursor-pointer group">
          <div>
            <p className="text-sm text-(--color-text) group-hover:text-(--color-primary) transition-colors">
              Database Viewer
            </p>
            <p className="text-xs text-(--color-text-muted)">
              Show the DB inspector in the sidebar. Lets you browse tables, run
              queries, and edit or delete rows.
            </p>
          </div>
          <input
            type="checkbox"
            checked={dbViewer}
            onChange={(e) => setDbViewer(e.target.checked)}
            className="w-4 h-4 accent-(--color-primary) cursor-pointer shrink-0"
          />
        </label>
      </section>
    </div>
  );
}
