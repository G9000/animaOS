import { useEffect, useRef } from "react";
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from "@xyflow/react";
import { NodeShell, type NodeAction } from "../../dashboard/nodes/NodeShell";
import type { RelationshipNode } from "./types";
import { LinkIcon } from "@anima/standard-templates";

const AT_RIGHT: React.CSSProperties = {
  position: "absolute",
  right: 0,
  top: "50%",
  transform: "translate(50%, -50%)",
  pointerEvents: "none",
};

export function AgentRelationshipNode({ data, id }: NodeProps<RelationshipNode>) {
  const updateNodeInternals = useUpdateNodeInternals();
  useEffect(() => { updateNodeInternals(id); }, [id, updateNodeInternals]);

  const {
    relationshipDraft,
    relationshipSaving,
    relationshipSaved,
    identityOverrideAllowed,
    onRelationshipChange,
    onRelationshipSave,
    onIdentityOverrideAllowedChange,
    onClose,
  } = data;

  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (identityOverrideAllowed) inputRef.current?.focus();
  }, [identityOverrideAllowed]);

  const actions: NodeAction[] = identityOverrideAllowed
    ? [{ id: "save", label: relationshipSaving ? "Saving..." : "Save", onClick: onRelationshipSave }]
    : [];

  return (
    <div style={{ position: "relative" }}>
      <NodeShell
        title="Relationship"
        icon={<LinkIcon size="sm" className="text-foreground/25" />}
        headerExtra={
          relationshipSaved
            ? <span className="font-mono text-[8px] tracking-[0.14em] uppercase text-accent/70">Saved</span>
            : null
        }
        onClose={onClose}
        actions={actions}
        required
        className="w-64"
        footer={
          <div className="px-3.5 py-2.5 flex items-center justify-between">
            <span className="font-mono text-[8px] tracking-[0.2em] uppercase text-foreground/30">Override</span>
            <input
              type="checkbox"
              checked={identityOverrideAllowed}
              onChange={(event) => onIdentityOverrideAllowedChange(event.target.checked)}
              className="nodrag h-3 w-3 accent-[var(--accent)]"
              aria-label="Toggle identity override"
            />
          </div>
        }
      >
        <div className="p-3 space-y-2">
          <p className="font-mono text-[9px] text-muted-foreground/50 leading-relaxed">
            Canonical relationship field. It cannot be changed after setup unless override is enabled.
          </p>
          <input
            ref={inputRef}
            readOnly={!identityOverrideAllowed}
            value={relationshipDraft}
            onChange={(event) => onRelationshipChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && identityOverrideAllowed) onRelationshipSave();
            }}
            placeholder="companion"
            className={`nodrag w-full bg-secondary/40 border border-border/40 px-2.5 py-2 font-mono text-[10px] outline-none transition-colors placeholder:text-muted-foreground/30 ${identityOverrideAllowed ? "text-foreground/70 focus:border-border cursor-text" : "text-foreground/30 cursor-default select-none"}`}
            aria-label="Agent relationship"
          />
          <p className="font-mono text-[8px] leading-relaxed text-accent/50">
            Override rewrites the canonical profile and supersedes the previous relationship memory.
          </p>
        </div>
      </NodeShell>

      <Handle
        type="source"
        position={Position.Right}
        style={{ ...AT_RIGHT, width: 14, height: 14, background: "var(--accent)", borderRadius: "50%", border: "3px solid var(--background)", zIndex: 9999, animation: "handle-pulse 2.4s ease-out infinite" }}
      />
    </div>
  );
}
