import { useState } from "react";
import { Icons } from "./Icons";
import { useLocalStorage } from "./hooks";

export interface SavedQuery {
  id: string;
  name: string;
  sql: string;
  description?: string;
  createdAt: number;
  lastRun?: number;
  runCount: number;
}

interface QueryFavoritesProps {
  onSelectQuery: (sql: string) => void;
  currentSql: string;
}

export function QueryFavorites({ onSelectQuery, currentSql }: QueryFavoritesProps) {
  const [savedQueries, setSavedQueries] = useLocalStorage<SavedQuery[]>("db-saved-queries", []);
  const [isOpen, setIsOpen] = useState(false);
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [newQueryName, setNewQueryName] = useState("");
  const [newQueryDesc, setNewQueryDesc] = useState("");

  const handleSave = () => {
    if (!newQueryName.trim() || !currentSql.trim()) return;
    
    const newQuery: SavedQuery = {
      id: crypto.randomUUID(),
      name: newQueryName.trim(),
      sql: currentSql.trim(),
      description: newQueryDesc.trim() || undefined,
      createdAt: Date.now(),
      runCount: 0,
    };
    
    setSavedQueries(prev => [newQuery, ...prev].slice(0, 50));
    setNewQueryName("");
    setNewQueryDesc("");
    setShowSaveDialog(false);
  };

  const handleDelete = (id: string) => {
    setSavedQueries(prev => prev.filter(q => q.id !== id));
  };

  const handleSelect = (query: SavedQuery) => {
    onSelectQuery(query.sql);
    setSavedQueries(prev =>
      prev.map(q =>
        q.id === query.id
          ? { ...q, lastRun: Date.now(), runCount: q.runCount + 1 }
          : q
      )
    );
    setIsOpen(false);
  };

  const canSaveCurrent = currentSql.trim().length > 0 && 
    !savedQueries.some(q => q.sql.trim() === currentSql.trim());

  return (
    <div className="relative">
      <div className="flex items-center gap-1">
        <button
          onClick={() => setIsOpen(!isOpen)}
          className={`flex items-center gap-1 px-2 py-1 text-xs rounded transition-colors ${
            savedQueries.length > 0
              ? "text-muted-foreground hover:text-foreground"
              : "text-muted-foreground/50"
          }`}
        >
          <Icons.Bookmark />
          Saved ({savedQueries.length})
        </button>
        
        {canSaveCurrent && (
          <button
            onClick={() => setShowSaveDialog(true)}
            className="p-1 text-primary hover:bg-primary/10 rounded"
            title="Save current query"
          >
            <Icons.Plus />
          </button>
        )}
      </div>

      {/* Dropdown */}
      {isOpen && (
        <div className="absolute left-0 top-full mt-1 w-80 bg-card border border-border rounded-none z-50 max-h-96 overflow-hidden flex flex-col">
          <div className="px-3 py-2 bg-input border-b border-border flex items-center justify-between">
            <span className="text-xs font-medium">Saved Queries</span>
            <button onClick={() => setIsOpen(false)} className="p-1 text-muted-foreground/50 hover:text-foreground">
              <Icons.X />
            </button>
          </div>
          
          {savedQueries.length === 0 ? (
            <div className="p-4 text-center text-muted-foreground/50 text-sm">
              No saved queries yet
            </div>
          ) : (
            <div className="overflow-y-auto flex-1">
              {savedQueries.map((query) => (
                <div
                  key={query.id}
                  className="px-3 py-2 border-b border-border/50 last:border-0 hover:bg-input group"
                >
                  <div className="flex items-start justify-between gap-2">
                    <button
                      onClick={() => handleSelect(query)}
                      className="flex-1 text-left"
                    >
                      <div className="text-sm font-medium truncate">{query.name}</div>
                      {query.description && (
                        <div className="text-[10px] text-muted-foreground/70 truncate">
                          {query.description}
                        </div>
                      )}
                      <div className="flex items-center gap-2 mt-1 text-[10px] text-muted-foreground/50">
                        <span>{new Date(query.createdAt).toLocaleDateString()}</span>
                        {query.runCount > 0 && (
                          <span>• Run {query.runCount} times</span>
                        )}
                      </div>
                    </button>
                    <button
                      onClick={() => handleDelete(query.id)}
                      className="opacity-0 group-hover:opacity-100 p-1 text-muted-foreground/50 hover:text-destructive transition-opacity"
                    >
                      <Icons.Trash />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Save Dialog */}
      {showSaveDialog && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="w-96 bg-card border border-border rounded-none p-4">
            <h3 className="text-sm font-medium mb-3">Save Query</h3>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground block mb-1">Name</label>
                <input
                  type="text"
                  value={newQueryName}
                  onChange={(e) => setNewQueryName(e.target.value)}
                  placeholder="e.g., Recent Messages"
                  className="w-full bg-input border border-border rounded px-3 py-2 text-sm"
                  autoFocus
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">Description (optional)</label>
                <input
                  type="text"
                  value={newQueryDesc}
                  onChange={(e) => setNewQueryDesc(e.target.value)}
                  placeholder="What does this query do?"
                  className="w-full bg-input border border-border rounded px-3 py-2 text-sm"
                />
              </div>
              <div className="text-xs text-muted-foreground/50 font-mono truncate">
                {currentSql.slice(0, 60)}{currentSql.length > 60 ? "..." : ""}
              </div>
              <div className="flex gap-2 justify-end">
                <button
                  onClick={() => setShowSaveDialog(false)}
                  className="px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  disabled={!newQueryName.trim()}
                  className="px-3 py-1.5 bg-primary text-white rounded text-xs hover:bg-primary/90 disabled:opacity-30"
                >
                  Save
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


