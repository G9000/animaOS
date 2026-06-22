import type { NodeProps } from "@xyflow/react";
import { DotLoader } from "@anima/standard-templates";
import type { GreetingNode } from "./node-types";
import { NodeShell } from "./NodeShell";

export function GreetingNode({ data }: NodeProps<GreetingNode>) {
  const { agentName, brief, briefLoading, userName, onChat, onClose } = data;
  const firstName = userName?.split(" ")[0];
  const message =
    brief?.message ??
    `Hi${firstName ? ` ${firstName}` : ""}, how can I help you today?`;

  return (
    <NodeShell title="Greeting" onClose={onClose} className="w-72">
      <div className="p-4">
        {briefLoading ? (
          <DotLoader />
        ) : (
          <div className="space-y-2 animate-fade-in">
            <h1 className="text-xl font-sans text-foreground leading-tight">
              {message}
            </h1>
            {brief?.llmGenerated && (
              <p className="font-mono text-[9px] tracking-[0.22em] uppercase text-muted-foreground/40">
                from {agentName}
              </p>
            )}
            <div className="text-right">
              <button
                onClick={onChat}
                className="mt-2 font-mono text-[9px] tracking-[0.15em] uppercase text-muted-foreground/40 hover:text-foreground transition-colors"
              >
                start chat →
              </button>
            </div>
          </div>
        )}
      </div>
    </NodeShell>
  );
}
