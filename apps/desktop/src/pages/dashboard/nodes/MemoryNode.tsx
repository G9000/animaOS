import type { NodeProps } from "@xyflow/react";
import type { MemoryNode } from "./node-types";
import { NodeShell } from "./NodeShell";

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
  const { episodes, agentName, avatarUrl, onChat, onRead, onViewAll, onClose } = data;

  return (
    <NodeShell
      title={`Memory Grid · ${episodes.length} episodes`}
      onClose={onClose}
      className="w-80"
      actions={
        episodes.length > 4
          ? [{ id: "view-all", label: "View all →", onClick: onViewAll }]
          : []
      }
    >
      <div className="p-3 space-y-2 max-h-80 overflow-y-auto pr-1">
        {episodes.slice(0, 4).map((ep) => {
          const title = ep.topics[0]
            ? ep.topics[0]
                .replace(/-/g, " ")
                .replace(/\b\w/g, (c) => c.toUpperCase())
            : ep.summary.slice(0, 40);
          return (
            <div
              key={ep.id}
              className="border border-border/60 p-2.5 flex flex-col gap-1.5 hover:border-border/90 transition-all"
            >
              <div className="flex items-center gap-2">
                {avatarUrl && (
                  <img
                    src={avatarUrl}
                    alt={agentName}
                    className="w-5 h-5 rounded-full object-cover border border-border shrink-0"
                  />
                )}
                <span className="text-xs font-semibold text-foreground/90 truncate">
                  {title}
                </span>
                <span className="font-mono text-[8px] text-muted-foreground/40 tracking-wider ml-auto">
                  {formatEpisodeDate(ep.date, ep.time)}
                </span>
              </div>
              <p
                className="text-xs leading-relaxed text-foreground/50 overflow-hidden"
                style={{
                  maskImage:
                    "linear-gradient(to bottom, black 40%, transparent 100%)",
                  WebkitMaskImage:
                    "linear-gradient(to bottom, black 40%, transparent 100%)",
                  maxHeight: "3rem",
                }}
              >
                {ep.summary}
              </p>
              <div className="flex items-center gap-1.5 flex-wrap mt-auto pt-1 border-t border-border/20">
                {ep.topics.slice(0, 2).map((t) => (
                  <span
                    key={t}
                    className="font-mono text-[8px] tracking-widest uppercase px-1 py-0.5 text-muted-foreground/50"
                  >
                    #{t}
                  </span>
                ))}
                <div className="flex-1" />
                <button
                  onClick={() => onChat(ep)}
                  className="font-mono text-[8px] tracking-wider text-accent/60 hover:text-accent transition-colors"
                >
                  chat →
                </button>
                {ep.summary.length > 80 && (
                  <button
                    onClick={() => onRead(ep)}
                    className="font-mono text-[8px] tracking-wider text-muted-foreground/40 hover:text-foreground transition-colors"
                  >
                    read →
                  </button>
                )}
              </div>
            </div>
          );
        })}

        {episodes.length === 0 && (
          <p className="font-mono text-[10px] text-muted-foreground/30 tracking-wider text-center py-4">
            No memories yet
          </p>
        )}
      </div>
    </NodeShell>
  );
}
