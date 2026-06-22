import type { NodeProps } from "@xyflow/react";
import { DotLoader } from "@anima/standard-templates";
import type { ReflectionNode } from "./node-types";
import { NodeShell } from "./NodeShell";

function formatEpisodeDate(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  return d.toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

export function ReflectionNode({ data }: NodeProps<ReflectionNode>) {
  const {
    agentName,
    reflection,
    reflectionLoading,
    onExplore,
    onExploreMemory,
    onClose,
  } = data;

  const isMemory =
    reflection?.curiosityType === "memory" &&
    reflection.sourceEpisodeId != null;

  function handleExplore() {
    if (!reflection) return;
    if (isMemory && reflection.sourceEpisodeId != null) {
      onExploreMemory(reflection.sourceEpisodeId);
    } else {
      onExplore(reflection.question ?? "");
    }
  }

  return (
    <NodeShell title="For Reflection" onClose={onClose} className="w-72">
      <div className="p-4">
        {reflectionLoading ? (
          <DotLoader />
        ) : reflection?.question ? (
          <div className="space-y-3 animate-fade-in">
            {isMemory && reflection.sourceEpisodeDate && (
              <div className="flex items-center gap-1.5">
                <span className="font-mono text-[9px] tracking-[0.20em] uppercase text-accent/60">
                  remembering · {formatEpisodeDate(reflection.sourceEpisodeDate)}
                </span>
              </div>
            )}
            <p className="text-sm text-foreground/80 leading-relaxed">
              {reflection.question}
            </p>
            <div className="flex items-center justify-between">
              <span className="font-mono text-[9px] tracking-[0.22em] uppercase text-muted-foreground/35">
                from {agentName}
              </span>
              <button
                onClick={handleExplore}
                className="font-mono text-[9px] tracking-[0.18em] uppercase text-muted-foreground/50 hover:text-foreground border border-border/35 hover:border-border/70 px-2.5 py-1 rounded transition-all duration-150"
              >
                explore →
              </button>
            </div>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground/20 italic">...</p>
        )}
      </div>
    </NodeShell>
  );
}
