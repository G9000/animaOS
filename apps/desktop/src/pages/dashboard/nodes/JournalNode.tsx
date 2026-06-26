import { NodeResizer, type NodeProps } from "@xyflow/react";
import type { JournalNode } from "./node-types";

function formatEntryDate(dateStr: string): string {
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return dateStr;
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "today";
  if (d.toDateString() === yesterday.toDateString()) return "yesterday";
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: d.getFullYear() !== today.getFullYear() ? "numeric" : undefined,
  });
}

export function JournalNode({ data }: NodeProps<JournalNode>) {
  const { entries, onNavigate, onNewEntry, onClose } = data;

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
          aria-label="Close journal"
        >
          ×
        </button>

        <div className="w-full h-full overflow-hidden rounded-xl bg-background/25 backdrop-blur-[36px] border border-foreground/[0.08] shadow-[0_4px_28px_rgba(0,0,0,0.18)] flex flex-col">

          {/* Header */}
          <div className="shrink-0 flex items-center justify-between px-3.5 h-9 border-b border-foreground/[0.06]">
            <span className="font-mono text-[8px] tracking-[0.3em] uppercase text-foreground/30">
              Journal
            </span>
            <div className="flex items-center gap-3">
              <span className="font-mono text-[8px] text-foreground/20">{entries.length}</span>
              <button
                onClick={onNewEntry}
                className="font-mono text-[7.5px] tracking-wider text-foreground/22 hover:text-foreground/55 transition-colors"
              >
                write +
              </button>
            </div>
          </div>

          {/* Entry list */}
          <div
            className="flex-1 min-h-0 overflow-y-auto nowheel divide-y divide-foreground/[0.05]"
            style={{ scrollbarWidth: "none" }}
          >
            {entries.length === 0 ? (
              <div className="flex items-center justify-center h-full">
                <div className="text-center space-y-2">
                  <p className="font-mono text-[8px] tracking-[0.24em] uppercase text-foreground/18">
                    no entries yet
                  </p>
                  <button
                    onClick={onNewEntry}
                    className="font-mono text-[7.5px] tracking-wider text-accent/50 hover:text-accent transition-colors"
                  >
                    write your first →
                  </button>
                </div>
              </div>
            ) : (
              entries.slice(0, 12).map((entry) => (
                <button
                  key={entry.id}
                  onClick={() => onNavigate("/journal")}
                  className="group/entry w-full flex text-left hover:bg-accent"
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
                  <div className="w-px shrink-0 self-stretch ml-3.5 bg-foreground/[0.07] group-hover/entry:bg-accent-dark transition-colors duration-150" />

                  <div className="flex-1 px-3 py-2.5 min-w-0 space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[7px] tracking-wider text-foreground/25 group-hover/entry:text-accent-foreground/60 transition-colors duration-150">
                        {formatEntryDate(entry.entryDate)}
                      </span>
                      {entry.mood && (
                        <span className="font-mono text-[7px] text-foreground/20 group-hover/entry:text-accent-foreground/50 ml-auto shrink-0 transition-colors duration-150">
                          {entry.mood}
                        </span>
                      )}
                    </div>
                    {entry.title && (
                      <p className="text-[11px] font-medium text-foreground/72 group-hover/entry:text-accent-foreground truncate leading-tight transition-colors duration-150">
                        {entry.title}
                      </p>
                    )}
                    <p
                      className="text-[10px] leading-relaxed text-foreground/38 group-hover/entry:text-accent-foreground/70 overflow-hidden transition-colors duration-150"
                      style={{
                        maskImage: "linear-gradient(to bottom, black 50%, transparent 100%)",
                        WebkitMaskImage: "linear-gradient(to bottom, black 50%, transparent 100%)",
                        maxHeight: "2.4rem",
                      }}
                    >
                      {entry.body}
                    </p>
                  </div>
                </button>
              ))
            )}
          </div>

          {/* Footer */}
          {entries.length > 12 && (
            <div className="shrink-0 px-3.5 h-8 border-t border-foreground/[0.05] flex items-center justify-between">
              <span className="font-mono text-[7px] tracking-wider text-foreground/18">
                +{entries.length - 12} more
              </span>
              <button
                onClick={() => onNavigate("/journal")}
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
