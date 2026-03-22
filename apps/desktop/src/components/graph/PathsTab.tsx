import type { SearchResults } from "./types";
import { RELATION_COLORS } from "./constants";

interface PathsTabProps {
  searchResults: SearchResults | null;
  onEntityClick: (entityId: number) => void;
}

function getRelationColor(type: string): string {
  return RELATION_COLORS[type] || "text-text-muted";
}

export function PathsTab({ searchResults, onEntityClick }: PathsTabProps) {
  if (searchResults === null) {
    return (
      <div className="text-center py-12">
        <p className="font-mono text-[10px] text-text-muted/30 tracking-wider">
          SEARCH_FOR_ENTITIES_TO_SEE_CONNECTIONS
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Matched Entities */}
      {searchResults.entities.length > 0 && (
        <div>
          <h3 className="font-mono text-[9px] text-text-muted/50 tracking-wider mb-2">
            MATCHED_ENTITIES ({searchResults.entities.length})
          </h3>
          <div className="flex flex-wrap gap-2">
            {searchResults.entities.map((entity) => (
              <button
                key={entity.id}
                onClick={() => onEntityClick(entity.id)}
                className="px-3 py-1.5 bg-bg-card border border-border hover:border-primary/30 transition-colors"
              >
                <span className="font-mono text-[10px] text-text">{entity.name}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Graph Paths */}
      {searchResults.paths.length > 0 ? (
        <div>
          <h3 className="font-mono text-[9px] text-text-muted/50 tracking-wider mb-2">
            CONNECTED_PATHS ({searchResults.paths.length})
          </h3>
          <div className="space-y-1">
            {searchResults.paths.map((path, i) => (
              <div
                key={i}
                className="flex items-center gap-2 px-3 py-2 bg-bg-card border border-border"
              >
                <span className="font-mono text-[10px] text-text">{path.source}</span>
                <span className="font-mono text-[8px] text-text-muted/30">({path.source_type})</span>
                <span className={`font-mono text-[9px] tracking-wider ${getRelationColor(path.relation)}`}>
                  {path.relation}
                </span>
                <span className="font-mono text-[10px] text-text">{path.destination}</span>
                <span className="font-mono text-[8px] text-text-muted/30">({path.destination_type})</span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <p className="font-mono text-[10px] text-text-muted/40 tracking-wider">
          NO_CONNECTIONS_FOUND
        </p>
      )}
    </div>
  );
}
