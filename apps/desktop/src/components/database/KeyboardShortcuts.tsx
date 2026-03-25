import { useEffect, useState } from "react";
import { Icons } from "./Icons";

interface KeyboardShortcutsProps {
  onNewQuery?: () => void;
  onRunQuery?: () => void;
  onSaveQuery?: () => void;
  onFocusSearch?: () => void;
  onToggleView?: () => void;
}

const shortcuts = [
  { key: "Ctrl + Enter", action: "Run query", scope: "Query editor" },
  { key: "Ctrl + K", action: "Command palette", scope: "Global" },
  { key: "Ctrl + F", action: "Focus search/filter", scope: "Tables/Data" },
  { key: "Ctrl + S", action: "Save query", scope: "Query editor" },
  { key: "Ctrl + R", action: "Refresh data", scope: "Global" },
  { key: "Esc", action: "Close modal/exit edit", scope: "Global" },
  { key: "← / →", action: "Prev/Next page", scope: "Data view" },
  { key: "V", action: "Toggle view mode", scope: "Data view" },
];

export function KeyboardShortcutsHelp() {
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ctrl/Cmd + ? to open shortcuts
      if ((e.ctrlKey || e.metaKey) && e.key === "/") {
        e.preventDefault();
        setIsOpen(true);
      }
      // Esc to close
      if (e.key === "Escape") {
        setIsOpen(false);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  if (!isOpen) {
    return (
      <button
        onClick={() => setIsOpen(true)}
        className="p-2 text-muted-foreground/50 hover:text-muted-foreground transition-colors"
        title="Keyboard shortcuts (Ctrl+?)"
      >
        <Icons.Keyboard />
      </button>
    );
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="w-[500px] max-h-[80vh] bg-card border border-border rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <h3 className="text-sm font-medium">Keyboard Shortcuts</h3>
          <button onClick={() => setIsOpen(false)} className="p-1 text-muted-foreground/50 hover:text-foreground">
            <Icons.X />
          </button>
        </div>
        <div className="p-4 overflow-auto">
          <div className="space-y-1">
            {shortcuts.map((shortcut, i) => (
              <div
                key={i}
                className="flex items-center justify-between py-2 px-3 hover:bg-input rounded"
              >
                <div>
                  <div className="text-sm">{shortcut.action}</div>
                  <div className="text-[10px] text-muted-foreground/60">{shortcut.scope}</div>
                </div>
                <kbd className="px-2 py-1 bg-input border border-border rounded text-xs font-mono">
                  {shortcut.key}
                </kbd>
              </div>
            ))}
          </div>
        </div>
        <div className="px-4 py-3 bg-input border-t border-border text-[11px] text-muted-foreground/60">
          Press <kbd className="px-1 py-0.5 bg-card border border-border rounded">Ctrl</kbd> +{" "}
          <kbd className="px-1 py-0.5 bg-card border border-border rounded">?</kbd> to show this help
        </div>
      </div>
    </div>
  );
}



// Hook for keyboard shortcuts
export function useKeyboardShortcuts({
  onNewQuery,
  onRunQuery,
  onSaveQuery,
  onFocusSearch,
  onToggleView,
}: KeyboardShortcutsProps = {}) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const isCtrl = e.ctrlKey || e.metaKey;

      // Ctrl+Enter - Run query
      if (isCtrl && e.key === "Enter" && onRunQuery) {
        e.preventDefault();
        onRunQuery();
      }

      // Ctrl+S - Save query
      if (isCtrl && e.key === "s" && onSaveQuery) {
        e.preventDefault();
        onSaveQuery();
      }

      // Ctrl+F - Focus search
      if (isCtrl && e.key === "f" && onFocusSearch) {
        e.preventDefault();
        onFocusSearch();
      }

      // Ctrl+R - Refresh
      if (isCtrl && e.key === "r") {
        e.preventDefault();
        window.location.reload();
      }

      // V - Toggle view (when not in input)
      if (e.key === "v" && !e.ctrlKey && !e.metaKey && !e.altKey && onToggleView) {
        const target = e.target as HTMLElement;
        if (target.tagName !== "INPUT" && target.tagName !== "TEXTAREA") {
          e.preventDefault();
          onToggleView();
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onNewQuery, onRunQuery, onSaveQuery, onFocusSearch, onToggleView]);
}
