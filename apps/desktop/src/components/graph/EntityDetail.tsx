import type { GraphEntityDetail } from "../../lib/api";
import { RELATION_COLORS } from "./constants";

interface EntityDetailProps {
  entity: GraphEntityDetail;
  onClose: () => void;
  onEntityClick: (entityId: number) => void;
}

function getRelationColor(type: string): string {
  return RELATION_COLORS[type] || "text-text-muted";
}

export function EntityDetail({ entity, onClose, onEntityClick }: EntityDetailProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg/80 backdrop-blur-sm">
      <div className="w-full max-w-2xl max-h-[80vh] bg-bg-card border border-border overflow-y-auto">
        {/* Header */}
        <div className="px-5 py-3 border-b border-border flex items-center justify-between">
          <div>
            <h2 className="font-mono text-lg text-text">{entity.name}</h2>
            <div className="flex items-center gap-2 mt-1">
              <span className="font-mono text-[9px] text-text-muted/40 tracking-wider">
                {entity.type.toUpperCase()}
              </span>
              <span className="text-border">|</span>
              <span className="font-mono text-[9px] text-text-muted/30">
                {entity.mentions} mentions
              </span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="font-mono text-[9px] text-text-muted/40 hover:text-text tracking-wider"
          >
            CLOSE
          </button>
        </div>

        {/* Description */}
        {entity.description && (
          <div className="px-5 py-3 border-b border-border">
            <p className="text-sm text-text-muted leading-relaxed">
              {entity.description}
            </p>
          </div>
        )}

        {/* Relations */}
        <div className="px-5 py-3 space-y-4">
          {/* Outgoing */}
          {entity.outgoingRelations.length > 0 && (
            <div>
              <h3 className="font-mono text-[9px] text-text-muted/50 tracking-wider mb-2">
                OUTGOING_RELATIONS ({entity.outgoingRelations.length})
              </h3>
              <div className="space-y-1">
                {entity.outgoingRelations.map((rel) => (
                  <button
                    key={rel.id}
                    onClick={() => rel.target && onEntityClick(rel.target.id)}
                    className="w-full flex items-center justify-between px-3 py-2 bg-bg-input border border-border hover:border-primary/30 transition-colors text-left"
                  >
                    <div className="flex items-center gap-2">
                      <span className={`font-mono text-[9px] tracking-wider ${getRelationColor(rel.type)}`}>
                        {rel.type}
                      </span>
                      <span className="font-mono text-[10px] text-text">→ {rel.target?.name}</span>
                      <span className="font-mono text-[8px] text-text-muted/40">
                        ({rel.target?.type})
                      </span>
                    </div>
                    <span className="font-mono text-[9px] text-text-muted/30">{rel.mentions}M</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Incoming */}
          {entity.incomingRelations.length > 0 && (
            <div>
              <h3 className="font-mono text-[9px] text-text-muted/50 tracking-wider mb-2">
                INCOMING_RELATIONS ({entity.incomingRelations.length})
              </h3>
              <div className="space-y-1">
                {entity.incomingRelations.map((rel) => (
                  <button
                    key={rel.id}
                    onClick={() => rel.source && onEntityClick(rel.source.id)}
                    className="w-full flex items-center justify-between px-3 py-2 bg-bg-input border border-border hover:border-primary/30 transition-colors text-left"
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[10px] text-text">{rel.source?.name}</span>
                      <span className="font-mono text-[8px] text-text-muted/40">
                        ({rel.source?.type})
                      </span>
                      <span className={`font-mono text-[9px] tracking-wider ${getRelationColor(rel.type)}`}>
                        → {rel.type}
                      </span>
                    </div>
                    <span className="font-mono text-[9px] text-text-muted/30">{rel.mentions}M</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {entity.outgoingRelations.length === 0 && entity.incomingRelations.length === 0 && (
            <p className="font-mono text-[10px] text-text-muted/40 tracking-wider">
              NO_RELATIONS_FOUND
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
