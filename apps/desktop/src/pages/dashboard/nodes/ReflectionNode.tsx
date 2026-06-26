import type { NodeProps } from "@xyflow/react";
import { DotLoader } from "@anima/standard-templates";
import type { ReflectionNode } from "./node-types";

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

  const handleExplore = () => {
    if (!reflection) return;
    if (isMemory && reflection.sourceEpisodeId != null) {
      onExploreMemory(reflection.sourceEpisodeId);
    } else {
      onExplore(reflection.question ?? "");
    }
  };

  return (
    <div className="group relative w-72 overflow-visible">

      {/* Close */}
      <button
        onClick={onClose}
        className="absolute -top-5 right-0 z-20 h-4 px-1.5 flex items-center rounded-sm bg-background/60 border border-foreground/[0.07] font-mono text-[8px] text-foreground/25 hover:text-foreground/60 hover:bg-background/80 opacity-0 group-hover:opacity-100 transition-all duration-200 backdrop-blur-sm"
        aria-label="Close reflection"
      >
        ×
      </button>

      {/* Glass card — consistent with all other nodes */}
      <div className="overflow-hidden rounded-xl bg-background/25 backdrop-blur-[36px] border border-foreground/[0.08] shadow-[0_4px_28px_rgba(0,0,0,0.18)]">

        {/* Header */}
        <div className="flex items-center justify-between px-3.5 h-9 border-b border-foreground/[0.06]">
          <span className="font-mono text-[8px] tracking-[0.3em] uppercase text-foreground/30">
            Reflection
          </span>
          {isMemory && reflection?.sourceEpisodeDate && (
            <span className="font-mono text-[7px] tracking-[0.16em] uppercase bg-accent/[0.1] text-accent/70 border border-accent/[0.18] px-1.5 py-0.5 rounded-sm">
              remembering · {formatEpisodeDate(reflection.sourceEpisodeDate)}
            </span>
          )}
        </div>

        {/* Body */}
        <div className="px-4 pt-4 pb-4">
          {reflectionLoading ? (
            <div className="py-3">
              <DotLoader />
            </div>
          ) : reflection?.question ? (
            <div className="space-y-4 animate-fade-in">

              <div className="relative pl-4">
                <span
                  className="absolute left-0 top-0 font-serif text-[28px] leading-none text-accent/30 select-none"
                  aria-hidden
                >
                  "
                </span>
                <p className="text-[13px] text-foreground/78 leading-relaxed">
                  {reflection.question}
                </p>
              </div>

              <div className="flex items-center justify-between pt-1">
                <span className="font-mono text-[7.5px] tracking-[0.24em] uppercase text-foreground/22">
                  from {agentName}
                </span>
                <button
                  onClick={handleExplore}
                  className="font-mono text-[8px] tracking-[0.2em] uppercase text-foreground/40 hover:text-foreground/80 border border-foreground/[0.09] hover:border-foreground/25 bg-foreground/[0.02] hover:bg-foreground/[0.05] px-3 py-1 rounded-sm transition-all duration-150"
                >
                  {isMemory ? "revisit →" : "explore →"}
                </button>
              </div>

            </div>
          ) : (
            <p className="text-xs text-foreground/15 italic py-2">…</p>
          )}
        </div>

      </div>
    </div>
  );
}
