import type { GraphEntityDetail } from "@anima/api-client";
import { RELATION_COLORS } from "./constants";

interface EntityDetailProps {
  entity: GraphEntityDetail;
  onClose: () => void;
  onEntityClick: (entityId: number) => void;
}

function getRelationColor(type: string): string {
  return RELATION_COLORS[type] || "text-muted-foreground";
}

export function EntityDetail({ entity, onClose, onEntityClick }: EntityDetailProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm">
      <div className="w-full max-w-2xl max-h-[80vh] bg-card border border-border overflow-y-auto">
        {/* Header */}
        <div className="px-5 py-3 border-b border-border flex items-center justify-between">
          <div>
            <h2 className="font-mono text-lg text-foreground">{entity.name}</h2>
            <div className="flex items-center gap-2 mt-1">
              <span className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
                {entity.type.toUpperCase()}
              </span>
              <span className="text-border">|</span>
              <span className="font-mono text-[9px] text-muted-foreground/30">
                {entity.mentions} mentions
              </span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="font-mono text-[9px] text-muted-foreground/40 hover:text-foreground tracking-wider"
          >
            CLOSE
          </button>
        </div>

        {/* Description */}
        {entity.description && (
          <div className="px-5 py-3 border-b border-border">
            <p className="text-sm text-muted-foreground leading-relaxed">
              {entity.description}
            </p>
          </div>
        )}

        {/* Relations */}
        <div className="px-5 py-3 space-y-4">
          {/* Outgoing */}
          {entity.outgoingRelations.length > 0 && (
            <div>
              <h3 className="font-mono text-[9px] text-muted-foreground/50 tracking-wider mb-2">
                OUTGOING_RELATIONS ({entity.outgoingRelations.length})
              </h3>
              <div className="space-y-1">
                {entity.outgoingRelations.map((rel) => (
                  <button
                    key={rel.id}
                    onClick={() => rel.target && onEntityClick(rel.target.id)}
                    className="w-full flex items-center justify-between px-3 py-2 bg-input border border-border hover:border-primary/30 transition-colors text-left"
                  >
                    <div className="flex items-center gap-2">
                      <span className={`font-mono text-[9px] tracking-wider ${getRelationColor(rel.type)}`}>
                        {rel.type}
                      </span>
                      <span className="font-mono text-[10px] text-foreground">→ {rel.target?.name}</span>
                      <span className="font-mono text-[8px] text-muted-foreground/40">
                        ({rel.target?.type})
                      </span>
                    </div>
                    <span className="font-mono text-[9px] text-muted-foreground/30">{rel.mentions}M</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Incoming */}
          {entity.incomingRelations.length > 0 && (
            <div>
              <h3 className="font-mono text-[9px] text-muted-foreground/50 tracking-wider mb-2">
                INCOMING_RELATIONS ({entity.incomingRelations.length})
              </h3>
              <div className="space-y-1">
                {entity.incomingRelations.map((rel) => (
                  <button
                    key={rel.id}
                    onClick={() => rel.source && onEntityClick(rel.source.id)}
                    className="w-full flex items-center justify-between px-3 py-2 bg-input border border-border hover:border-primary/30 transition-colors text-left"
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[10px] text-foreground">{rel.source?.name}</span>
                      <span className="font-mono text-[8px] text-muted-foreground/40">
                        ({rel.source?.type})
                      </span>
                      <span className={`font-mono text-[9px] tracking-wider ${getRelationColor(rel.type)}`}>
                        → {rel.type}
                      </span>
                    </div>
                    <span className="font-mono text-[9px] text-muted-foreground/30">{rel.mentions}M</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {entity.outgoingRelations.length === 0 && entity.incomingRelations.length === 0 && (
            <p className="font-mono text-[10px] text-muted-foreground/40 tracking-wider">
              NO_RELATIONS_FOUND
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
