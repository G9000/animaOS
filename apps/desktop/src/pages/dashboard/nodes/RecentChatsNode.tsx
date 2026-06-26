import { NodeResizer, type NodeProps } from "@xyflow/react";
import type { RecentChatsNode } from "./node-types";
import { formatThreadTimestamp } from "../helpers";

export function RecentChatsNode({ data }: NodeProps<RecentChatsNode>) {
  const { threads, onOpenThread, onNewChat, onClose } = data;

  const recent = [...threads]
    .sort((a, b) => {
      const ta = a.lastMessageAt ?? a.createdAt ?? "";
      const tb = b.lastMessageAt ?? b.createdAt ?? "";
      return tb.localeCompare(ta);
    })
    .slice(0, 10);

  return (
    <>
      <NodeResizer
        minWidth={220}
        minHeight={160}
        maxWidth={480}
        maxHeight={700}
        lineStyle={{ borderColor: "var(--border)", borderWidth: 1, opacity: 0.35 }}
        handleStyle={{
          width: 8, height: 8, borderRadius: 2,
          border: "1px solid var(--border)",
          background: "var(--background)",
          opacity: 0.55,
        }}
      />

      <div className="group relative w-full h-full overflow-visible">

        <button
          onClick={onClose}
          className="absolute -top-5 right-0 z-20 h-4 px-1.5 flex items-center rounded-sm bg-background/60 border border-foreground/[0.07] font-mono text-[8px] text-foreground/25 hover:text-foreground/60 hover:bg-background/80 opacity-0 group-hover:opacity-100 transition-all duration-200 backdrop-blur-sm"
          aria-label="Close chats"
        >
          ×
        </button>

        <div className="w-full h-full overflow-hidden rounded-xl bg-background/25 backdrop-blur-[36px] border border-foreground/[0.08] shadow-[0_4px_28px_rgba(0,0,0,0.18)] flex flex-col">

          {/* Header */}
          <div className="shrink-0 flex items-center justify-between px-3.5 h-9 border-b border-foreground/[0.06]">
            <span className="font-mono text-[8px] tracking-[0.3em] uppercase text-foreground/30">
              Chats
            </span>
            <div className="flex items-center gap-3">
              <span className="font-mono text-[8px] text-foreground/20">{recent.length}</span>
              <button
                onClick={onNewChat}
                className="font-mono text-[7.5px] tracking-wider text-foreground/22 hover:text-foreground/55 transition-colors"
              >
                new +
              </button>
            </div>
          </div>

          {/* Thread list */}
          <div
            className="flex-1 min-h-0 overflow-y-auto nowheel divide-y divide-foreground/[0.05]"
            style={{ scrollbarWidth: "none" }}
          >
            {recent.length === 0 ? (
              <div className="flex items-center justify-center h-full">
                <div className="text-center space-y-2">
                  <p className="font-mono text-[8px] tracking-[0.24em] uppercase text-foreground/18">
                    no conversations yet
                  </p>
                  <button
                    onClick={onNewChat}
                    className="font-mono text-[7.5px] tracking-wider text-accent/50 hover:text-accent transition-colors"
                  >
                    start one →
                  </button>
                </div>
              </div>
            ) : (
              recent.map((thread) => (
                <button
                  key={thread.id}
                  onClick={() => onOpenThread(thread.id)}
                  className="group/thread w-full flex text-left hover:bg-accent"
                  style={{ transition: "transform 150ms ease, box-shadow 150ms ease, background-color 150ms ease" }}
                  onMouseEnter={(e) => {
                    const el = e.currentTarget as HTMLElement;
                    el.style.transform = "translateX(6px)";
                    el.style.boxShadow = "-2px 2px 0 var(--color-accent-dark), -4px 4px 0 color-mix(in oklch, var(--color-accent-dark) 50%, transparent)";
                  }}
                  onMouseLeave={(e) => {
                    const el = e.currentTarget as HTMLElement;
                    el.style.transform = "";
                    el.style.boxShadow = "";
                  }}
                >
                  <div className="w-px shrink-0 self-stretch ml-3.5 bg-foreground/[0.07] group-hover/thread:bg-accent-dark transition-colors duration-150" />

                  <div className="flex-1 px-3 py-2.5 min-w-0">
                    <div className="flex items-start gap-2">
                      <span className="text-[11px] font-medium text-foreground/72 group-hover/thread:text-accent-foreground truncate leading-tight transition-colors duration-150">
                        {thread.title ?? "Untitled conversation"}
                      </span>
                      <span className="font-mono text-[7px] text-foreground/22 group-hover/thread:text-accent-foreground/60 tracking-wider ml-auto shrink-0 mt-px transition-colors duration-150">
                        {formatThreadTimestamp(thread)}
                      </span>
                    </div>
                    <p className="font-mono text-[7px] tracking-[0.14em] uppercase text-foreground/20 group-hover/thread:text-accent-foreground/55 mt-1 transition-colors duration-150">
                      {thread.status === "active" ? "● active" : "○ closed"}
                    </p>
                  </div>
                </button>
              ))
            )}
          </div>

        </div>
      </div>
    </>
  );
}
