import { useEffect } from "react";
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from "@xyflow/react";
import { NodeShell } from "../../dashboard/nodes/NodeShell";
import type { BirthdayNode } from "./types";

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

  const { agentBirthday, onClose } = data;

  return (
    <div style={{ position: "relative" }}>
      <NodeShell
        title="Agent Birthday"
        onClose={onClose}
        required
        className="w-56"
      >
        <div className="p-3 space-y-2">
          <p className="font-mono text-[9px] text-muted-foreground/50 leading-relaxed">
            Created from the agent profile record.
          </p>
          <div className="w-full bg-secondary/40 border border-border/40 px-2.5 py-2 font-mono text-[10px] text-foreground/70">
            {formatAgentBirthday(agentBirthday)}
          </div>
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
