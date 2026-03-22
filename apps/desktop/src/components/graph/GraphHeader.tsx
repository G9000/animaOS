import type { GraphOverviewData } from "../../lib/api";

interface GraphHeaderProps {
  overview: GraphOverviewData | null;
  searchQuery: string;
  searchResults: { entities: unknown[]; paths: unknown[] } | null;
  loading: boolean;
  onSearchChange: (value: string) => void;
  onSearch: () => void;
  onClear: () => void;
}

export function GraphHeader({
  overview,
  searchQuery,
  searchResults,
  loading,
  onSearchChange,
  onSearch,
  onClear,
}: GraphHeaderProps) {
  return (
    <div className="px-5 py-2.5 border-b border-border bg-bg-card/40">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="font-mono text-[10px] text-text-muted tracking-wider">
            KNOWLEDGE_GRAPH
          </span>
          {overview && (
            <>
              <div className="w-px h-3 bg-border" />
              <span className="font-mono text-[9px] text-text-muted/40 tracking-wider">
                {overview.entityCount} ENTITIES // {overview.relationCount} RELATIONS
              </span>
            </>
          )}
        </div>
        <form
          onSubmit={(e) => { e.preventDefault(); onSearch(); }}
          className="flex items-center gap-1.5"
        >
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search entities..."
            className="w-40 bg-bg-input border border-border px-2 py-0.5 font-mono text-[10px] text-text placeholder:text-text-muted/20 outline-none focus:border-primary/40 focus:w-52 transition-all"
          />
          <button
            type="submit"
            disabled={!searchQuery.trim() || loading}
            className="font-mono text-[9px] text-text-muted/40 hover:text-text-muted tracking-wider disabled:opacity-30"
          >
            SEARCH
          </button>
          {searchResults && (
            <button
              type="button"
              onClick={onClear}
              className="font-mono text-[9px] text-text-muted/30 hover:text-text-muted tracking-wider"
            >
              CLR
            </button>
          )}
        </form>
      </div>
    </div>
  );
}
