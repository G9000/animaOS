import type { GraphOverviewData } from "../../lib/api";
import { RELATION_COLORS } from "./constants";

interface OverviewTabProps {
  overview: GraphOverviewData;
  onEntityClick: (entityId: number) => void;
  onFilterClick: (type: string) => void;
}

function getRelationColor(type: string): string {
  return RELATION_COLORS[type] || "text-text-muted";
}

export function OverviewTab({ overview, onEntityClick, onFilterClick }: OverviewTabProps) {
  return (
    <div className="space-y-6 max-w-2xl">
      {/* Stats */}
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-bg-card border border-border px-4 py-3">
          <div className="font-mono text-[9px] text-text-muted/40 tracking-wider mb-1">
            TOTAL_ENTITIES
          </div>
          <div className="text-2xl font-mono text-text">{overview.entityCount}</div>
        </div>
        <div className="bg-bg-card border border-border px-4 py-3">
          <div className="font-mono text-[9px] text-text-muted/40 tracking-wider mb-1">
            TOTAL_RELATIONS
          </div>
          <div className="text-2xl font-mono text-text">{overview.relationCount}</div>
        </div>
      </div>

      {/* Type Distribution */}
      {Object.keys(overview.typeDistribution).length > 0 && (
        <div>
          <h3 className="font-mono text-[9px] text-text-muted/50 tracking-wider mb-3">
            ENTITY_TYPES
          </h3>
          <div className="flex flex-wrap gap-2">
            {Object.entries(overview.typeDistribution).map(([type, count]) => (
              <button
                key={type}
                onClick={() => onFilterClick(type)}
                className="px-3 py-1.5 bg-bg-card border border-border hover:border-primary/30 transition-colors"
              >
                <span className="font-mono text-[9px] text-text-muted/40 tracking-wider mr-2">
                  {type.toUpperCase()}
                </span>
                <span className="font-mono text-[10px] text-primary">{count}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Relation Type Distribution */}
      {Object.keys(overview.relationTypeDistribution).length > 0 && (
        <div>
          <h3 className="font-mono text-[9px] text-text-muted/50 tracking-wider mb-3">
            RELATION_TYPES
          </h3>
          <div className="flex flex-wrap gap-2">
            {Object.entries(overview.relationTypeDistribution)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 15)
              .map(([type, count]) => (
                <div
                  key={type}
                  className="px-3 py-1.5 bg-bg-card border border-border"
                >
                  <span className={`font-mono text-[9px] tracking-wider mr-2 ${getRelationColor(type)}`}>
                    {type}
                  </span>
                  <span className="font-mono text-[10px] text-text-muted">{count}</span>
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Top Entities */}
      {overview.topEntities.length > 0 && (
        <div>
          <h3 className="font-mono text-[9px] text-text-muted/50 tracking-wider mb-3">
            TOP_ENTITIES
          </h3>
          <div className="space-y-1">
            {overview.topEntities.map((entity) => (
              <button
                key={entity.id}
                onClick={() => onEntityClick(entity.id)}
                className="w-full flex items-center justify-between px-3 py-2 bg-bg-card border border-border hover:border-primary/30 transition-colors text-left"
              >
                <div className="flex items-center gap-3">
                  <span className="font-mono text-[10px] text-text">{entity.name}</span>
                  <span className="font-mono text-[8px] text-text-muted/40 tracking-wider">
                    {entity.type.toUpperCase()}
                  </span>
                </div>
                <span className="font-mono text-[9px] text-text-muted/30">
                  {entity.mentions}M
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
