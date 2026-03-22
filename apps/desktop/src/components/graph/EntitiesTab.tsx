import type { GraphEntity } from "../../lib/api";
import { ENTITY_TYPES, ENTITY_LIMIT } from "./constants";

interface EntitiesTabProps {
  entities: GraphEntity[];
  entityFilter: string;
  entityOffset: number;
  entityTotal: number;
  loading: boolean;
  onFilterChange: (filter: string) => void;
  onEntityClick: (entityId: number) => void;
  onOffsetChange: (offset: number) => void;
}

export function EntitiesTab({
  entities,
  entityFilter,
  entityOffset,
  entityTotal,
  loading,
  onFilterChange,
  onEntityClick,
  onOffsetChange,
}: EntitiesTabProps) {
  return (
    <div className="space-y-4">
      {/* Filter */}
      <div className="flex items-center gap-2">
        <span className="font-mono text-[9px] text-text-muted/40 tracking-wider">FILTER:</span>
        <div className="flex gap-px">
          {ENTITY_TYPES.map((t) => (
            <button
              key={t.key}
              onClick={() => onFilterChange(t.key)}
              className={`px-2 py-1 font-mono text-[9px] tracking-wider transition-colors ${
                entityFilter === t.key
                  ? "bg-primary/[0.08] text-primary"
                  : "bg-bg-input text-text-muted/40 hover:text-text-muted"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Entity List */}
      {loading ? (
        <div className="font-mono text-[10px] text-text-muted/40 animate-pulse tracking-wider">
          LOADING...
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-1">
            {entities.map((entity) => (
              <button
                key={entity.id}
                onClick={() => onEntityClick(entity.id)}
                className="flex items-center justify-between px-3 py-2 bg-bg-card border border-border hover:border-primary/30 transition-colors text-left"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-mono text-[11px] text-text truncate">{entity.name}</span>
                  <span className="font-mono text-[8px] text-text-muted/40 tracking-wider shrink-0">
                    {entity.type.toUpperCase()}
                  </span>
                </div>
                <span className="font-mono text-[9px] text-text-muted/30 shrink-0">
                  {entity.mentions}M
                </span>
              </button>
            ))}
          </div>

          {/* Pagination */}
          {entityTotal > ENTITY_LIMIT && (
            <div className="flex items-center justify-between pt-2">
              <button
                onClick={() => onOffsetChange(Math.max(0, entityOffset - ENTITY_LIMIT))}
                disabled={entityOffset === 0}
                className="font-mono text-[9px] text-text-muted/40 hover:text-text-muted disabled:opacity-30 tracking-wider"
              >
                PREV
              </button>
              <span className="font-mono text-[9px] text-text-muted/30">
                {entityOffset + 1}-{Math.min(entityOffset + ENTITY_LIMIT, entityTotal)} / {entityTotal}
              </span>
              <button
                onClick={() => onOffsetChange(entityOffset + ENTITY_LIMIT)}
                disabled={entityOffset + ENTITY_LIMIT >= entityTotal}
                className="font-mono text-[9px] text-text-muted/40 hover:text-text-muted disabled:opacity-30 tracking-wider"
              >
                NEXT
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
