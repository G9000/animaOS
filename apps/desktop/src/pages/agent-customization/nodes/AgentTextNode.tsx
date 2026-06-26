import { useEffect } from "react";
import { Handle, NodeResizer, Position, useUpdateNodeInternals, type NodeProps } from "@xyflow/react";
import { NodeShell, type NodeAction } from "../../dashboard/nodes/NodeShell";
import { TA, type TextNode } from "./types";

const AT_RIGHT: React.CSSProperties = {
  position: "absolute",
  right: 0,
  top: "50%",
  transform: "translate(50%, -50%)",
  pointerEvents: "none",
};

export function AgentTextNode({ data, id }: NodeProps<TextNode>) {
  const updateNodeInternals = useUpdateNodeInternals();
  useEffect(() => { updateNodeInternals(id); }, [id, updateNodeInternals]);

  const {
    nodeTitle, description, draft, version, loading,
    saving, saved, hasWarmer, readOnly, required, requiresOverride,
    identityOverrideAllowed, overrideDescription,
    onChange, onSave, onWarmer, onIdentityOverrideAllowedChange, onClose,
    cardWidth, inputRows,
  } = data;

  const isLarge = (inputRows ?? 6) > 6;
  const lockedByOverride = Boolean(requiresOverride && !identityOverrideAllowed);
  const canEdit = !readOnly && !lockedByOverride;

  const actions: NodeAction[] = [
    ...(hasWarmer && canEdit ? [{ id: "warmer", label: "Warmer", onClick: onWarmer }] : []),
    ...(canEdit ? [{ id: "save", label: saving ? "Saving..." : "Save", onClick: onSave }] : []),
  ];

  const taClass = isLarge
    ? [
        "nodrag nowheel w-full resize-none flex-1 min-h-0",
        "bg-foreground/[0.04] border border-foreground/[0.08]",
        "px-4 py-3.5 font-mono text-[13px] leading-relaxed text-foreground/80",
        "outline-none focus:border-foreground/20 transition-colors",
        "placeholder:text-foreground/[0.18] disabled:opacity-40",
      ].join(" ")
    : TA;

  return (
    <div style={{ position: "relative", ...(isLarge && { width: "100%", height: "100%" }) }}>
      {isLarge && (
        <NodeResizer
          minWidth={340}
          minHeight={220}
          lineStyle={{ borderColor: "color-mix(in oklch, var(--foreground) 8%, transparent)", borderWidth: 1 }}
          handleStyle={{ width: 7, height: 7, borderRadius: 2, border: "1px solid color-mix(in oklch, var(--foreground) 12%, transparent)", background: "var(--background)", opacity: 0.8 }}
        />
      )}
      <NodeShell
        title={nodeTitle}
        headerExtra={
          <div className="flex items-center gap-2">
            {version != null && (
              <span className="font-mono text-[7px] text-muted-foreground/40 border border-border/40 px-1 py-0.5">
                v{version}
              </span>
            )}
            {saved && (
              <span className="font-mono text-[8px] tracking-[0.14em] uppercase text-accent/70">Saved</span>
            )}
          </div>
        }
        onClose={onClose}
        actions={actions}
        required={required ?? true}
        fluid={isLarge}
        className={isLarge ? "w-full h-full" : (cardWidth ?? "w-[340px]")}
        footer={
          <div className="px-3.5 py-2 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              {requiresOverride && onIdentityOverrideAllowedChange && (
                <label className="nodrag flex items-center gap-1.5 font-mono text-[8px] tracking-[0.18em] uppercase text-accent/55">
                  <input
                    type="checkbox"
                    checked={identityOverrideAllowed}
                    onChange={(event) => onIdentityOverrideAllowedChange(event.target.checked)}
                    className="h-3 w-3 accent-[var(--accent)]"
                  />
                  Override
                </label>
              )}
              {readOnly && (
                <span className="font-mono text-[8px] tracking-[0.18em] uppercase text-foreground/25">
                  Read only
                </span>
              )}
            </div>
            <div className="flex items-center justify-end gap-2.5">
              <span className="font-mono text-[8px] text-muted-foreground/30">
                {draft.length.toLocaleString()} chars
              </span>
              <span className="font-mono text-[8px] text-muted-foreground/20">/</span>
              <span className="font-mono text-[8px] text-muted-foreground/30">
                ~{Math.round(draft.length / 4).toLocaleString()} tokens
              </span>
            </div>
          </div>
        }
      >
        <div className={isLarge ? "p-4 flex flex-col gap-3 h-full min-h-0" : "p-3 space-y-2"}>
          <p className={isLarge ? "shrink-0 font-mono text-[10px] text-foreground/35 leading-relaxed" : "font-mono text-[9px] text-muted-foreground/50 leading-relaxed"}>
            {description}
          </p>
          {lockedByOverride && (
            <p className="shrink-0 font-mono text-[8.5px] leading-relaxed text-accent/50">
              This node cannot be changed unless override is enabled.
              {overrideDescription ? ` ${overrideDescription}` : ""}
            </p>
          )}
          <textarea
            value={draft}
            rows={isLarge ? undefined : (inputRows ?? 6)}
            readOnly={readOnly || lockedByOverride}
            disabled={loading || saving}
            onChange={(event) => onChange(event.target.value)}
            placeholder="..."
            className={taClass}
          />
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
