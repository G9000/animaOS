import { useEffect, useRef } from "react";
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from "@xyflow/react";
import { NodeShell, type NodeAction } from "@anima/standard-templates";
import type { BirthdayNode } from "./types";
import { SparkleIcon } from "@anima/standard-templates";

const AT_RIGHT: React.CSSProperties = {
  position: "absolute",
  right: 0,
  top: "50%",
  transform: "translate(50%, -50%)",
  pointerEvents: "none",
};

export function AgentBirthdayNode({ data, id }: NodeProps<BirthdayNode>) {
  const updateNodeInternals = useUpdateNodeInternals();
  useEffect(() => { updateNodeInternals(id); }, [id, updateNodeInternals]);

  const {
    agentBirthday,
    agentBirthdayDraft,
    agentBirthdaySaving,
    agentBirthdaySaved,
    identityOverrideAllowed,
    onAgentBirthdayChange,
    onAgentBirthdaySave,
    onIdentityOverrideAllowedChange,
    onClose,
  } = data;

  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (identityOverrideAllowed) inputRef.current?.focus();
  }, [identityOverrideAllowed]);

  const actions: NodeAction[] = identityOverrideAllowed
    ? [{ id: "save", label: agentBirthdaySaving ? "Saving..." : "Save", onClick: onAgentBirthdaySave }]
    : [];

  return (
    <div style={{ position: "relative" }}>
      <NodeShell
        title="Agent Birthday"
        icon={<SparkleIcon size="sm" className="text-foreground/25" />}
        headerExtra={
          agentBirthdaySaved
            ? <span className="font-mono text-micro tracking-caps-2 uppercase text-accent/70">Saved</span>
            : null
        }
        onClose={onClose}
        actions={actions}
        required
        className="w-64"
        footer={
          <div className="px-3.5 py-2.5 flex items-center justify-between">
            <span className="font-mono text-micro tracking-caps-4 uppercase text-foreground/30">Override</span>
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
          <p className="font-mono text-label text-muted-foreground/50 leading-relaxed">
            Canonical birth timestamp. It cannot be changed after setup unless override is enabled.
          </p>
          <input
            ref={inputRef}
            type="datetime-local"
            step={1}
            readOnly={!identityOverrideAllowed}
            value={agentBirthdayDraft}
            onChange={(event) => onAgentBirthdayChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && identityOverrideAllowed) onAgentBirthdaySave();
            }}
            className={`nodrag w-full bg-secondary/40 border border-border/40 px-2.5 py-2 font-mono text-caption outline-none transition-colors [color-scheme:dark] ${identityOverrideAllowed ? "text-foreground/70 focus:border-border cursor-text" : "text-foreground/30 cursor-default select-none"}`}
            aria-label="Agent birthday"
          />
          <p className="font-mono text-micro leading-relaxed text-accent/50">
            Override changes the displayed agent birthday, while the original creation time remains stored.
          </p>
          {!agentBirthdayDraft && (
            <p className="font-mono text-micro leading-relaxed text-foreground/30">
              Current: {formatAgentBirthday(agentBirthday)}
            </p>
          )}
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

function formatAgentBirthday(value: string): string {
  if (!value) return "Not recorded";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;

  const pad = (part: number) => part.toString().padStart(2, "0");
  return [
    `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}`,
    `${pad(parsed.getHours())}:${pad(parsed.getMinutes())}:${pad(parsed.getSeconds())}`,
  ].join(" ");
}
