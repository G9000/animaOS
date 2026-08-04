import { NodeResizer, type NodeProps } from "@xyflow/react";
import { cn, liftXAccent, glassPanel } from "@anima/standard-templates";
import type { MemoryNode } from "./node-types";

function formatEpisodeDate(dateStr: string, timeStr: string | null): string {
  const d = new Date(timeStr ? `${dateStr}T${timeStr}` : dateStr);
  if (Number.isNaN(d.getTime())) return dateStr;
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: d.getFullYear() !== new Date().getFullYear() ? "numeric" : undefined,
  });
}

export function MemoryNode({ data }: NodeProps<MemoryNode>) {
  const { episodes, onChat, onRead, onViewAll, onClose } = data;

  return (
    <>
      <NodeResizer
        minWidth={240}
        minHeight={160}
        maxWidth={520}
        maxHeight={800}
        lineStyle={{ borderColor: "var(--border)", borderWidth: 1, opacity: 0.35 }}
        handleStyle={{
          width: 8,
          height: 8,
          borderRadius: 2,
          border: "1px solid var(--border)",
          background: "var(--background)",
          opacity: 0.55,
        }}
      />

      <div className="group relative w-full h-full overflow-visible">

        {/* Close */}
        <button
          onClick={onClose}
          className="absolute -top-5 right-0 z-20 h-4 px-1.5 flex items-center rounded-sm bg-background/60 border border-hairline-faint font-mono text-micro text-foreground/25 hover:text-foreground/60 hover:bg-background/80 opacity-0 group-hover:opacity-100 transition-all duration-200 backdrop-blur-sm"
          aria-label="Close memories"
        >
          ×
        </button>

        {/* Glass card */}
        <div className={cn(glassPanel, "w-full h-full overflow-hidden flex flex-col")}>

          {/* Header */}
          <div className="shrink-0 flex items-center justify-between px-3.5 h-9 border-b border-hairline-faint">
            <span className="font-mono text-micro tracking-caps-5 uppercase text-foreground/30">
              Memories
            </span>
            <span className="font-mono text-micro text-foreground/20">
              {episodes.length}
            </span>
          </div>

          {/* Episode list — flex-1 so it fills the resized height */}
          <div
            className="flex-1 min-h-0 overflow-y-auto nowheel divide-y divide-foreground/[0.05]"
            style={{ scrollbarWidth: "none" }}
          >
            {episodes.length === 0 ? (
              <div className="px-4 py-8 text-center">
                <p className="font-mono text-micro tracking-caps-4 uppercase text-foreground/18">
                  no memories yet
                </p>
              </div>
            ) : (
              episodes.slice(0, 12).map((ep) => {
                const title = ep.topics[0]
                  ? ep.topics[0]
                      .replace(/-/g, " ")
                      .replace(/\b\w/g, (c) => c.toUpperCase())
                  : ep.summary.slice(0, 40);

                return (
                  <div
                    key={ep.id}
                    className={cn(liftXAccent, "group/ep flex hover:bg-accent transition-colors")}
                  >
                    {/* Left accent bar — switches to accent-dark so it stays visible on accent bg */}
                    <div className="w-px shrink-0 self-stretch ml-3.5 bg-foreground/[0.07] group-hover/ep:bg-accent-dark transition-colors duration-150" />

                    <div className="flex-1 px-3 py-3 min-w-0 space-y-1.5">

                      {/* Title + date */}
                      <div className="flex items-start gap-2">
                        <span className="text-detail font-medium text-foreground/72 group-hover/ep:text-accent-foreground leading-tight truncate transition-colors duration-150">
                          {title}
                        </span>
                        <span className="font-mono text-nano text-foreground/22 group-hover/ep:text-accent-foreground/60 tracking-wider ml-auto shrink-0 mt-px transition-colors duration-150">
                          {formatEpisodeDate(ep.date, ep.time)}
                        </span>
                      </div>

                      {/* Summary with fade */}
                      <p
                        className="text-caption leading-relaxed text-foreground/38 group-hover/ep:text-accent-foreground/70 overflow-hidden transition-colors duration-150"
                        style={{
                          maskImage:
                            "linear-gradient(to bottom, black 40%, transparent 100%)",
                          WebkitMaskImage:
                            "linear-gradient(to bottom, black 40%, transparent 100%)",
                          maxHeight: "2.6rem",
                        }}
                      >
                        {ep.summary}
                      </p>

                      {/* Tags + actions */}
                      <div className="flex items-center gap-1.5 flex-wrap pt-0.5">
                        {ep.topics.slice(0, 2).map((t) => (
                          <span
                            key={t}
                            className="font-mono text-nano tracking-caps-3 uppercase text-foreground/22 group-hover/ep:text-accent-foreground/60 border border-hairline-faint group-hover/ep:border-accent-foreground/20 px-1 py-px rounded-sm transition-colors duration-150"
                          >
                            {t}
                          </span>
                        ))}
                        <div className="flex-1" />
                        <button
                          onClick={() => onChat(ep)}
                          className="font-mono text-[7.5px] tracking-wider text-accent/55 group-hover/ep:text-accent-foreground transition-colors duration-150"
                        >
                          chat →
                        </button>
                        {ep.summary.length > 80 && (
                          <button
                            onClick={() => onRead(ep)}
                            className="font-mono text-[7.5px] tracking-wider text-foreground/22 group-hover/ep:text-accent-foreground/70 transition-colors duration-150"
                          >
                            read →
                          </button>
                        )}
                      </div>

                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* Footer — view all */}
          {episodes.length > 12 && (
            <div className="shrink-0 px-3.5 h-8 border-t border-hairline-faint flex items-center justify-between">
              <span className="font-mono text-nano tracking-wider text-foreground/18">
                +{episodes.length - 12} more
              </span>
              <button
                onClick={onViewAll}
                className="font-mono text-[7.5px] tracking-wider text-foreground/25 hover:text-foreground/55 transition-colors"
              >
                view all →
              </button>
            </div>
          )}

        </div>
      </div>
    </>
  );
}
