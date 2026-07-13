import type { NodeProps } from "@xyflow/react";
import type { NudgeNode } from "./node-types";
import { NodeShell } from "@anima/standard-templates";

export function NudgeNode({ data }: NodeProps<NudgeNode>) {
  const { nudges, onDismiss, onClose } = data;

  if (nudges.length === 0) return null;

  return (
    <NodeShell
      title={
        <span className="font-mono text-[10px] tracking-[0.22em] uppercase text-primary/70">
          Urgent matter
        </span>
      }
      onClose={onClose}
      className="w-72"
    >
      <div className="p-3 space-y-2">
        <div className="flex items-center justify-between gap-2 border-b border-border/60 pb-2">
          <span className="font-mono text-[8px] tracking-[0.18em] uppercase text-destructive/70">
            alert
          </span>
          <button
            onClick={() => onDismiss(nudges[0].type)}
            className="font-mono text-[9px] text-muted-foreground/30 hover:text-muted-foreground tracking-wider"
          >
            DISMISS ALL
          </button>
        </div>
        {nudges.map((nudge) => (
          <div
            key={nudge.type}
            className="flex items-start justify-between gap-3"
          >
            <span className="text-xs text-foreground/70 leading-relaxed">
              {nudge.message}
            </span>
            <button
              onClick={() => onDismiss(nudge.type)}
              className="font-mono text-[9px] text-muted-foreground/30 hover:text-muted-foreground shrink-0 tracking-wider"
            >
              DISMISS
            </button>
          </div>
        ))}
      </div>
    </NodeShell>
  );
}
