import { NodeResizer, type NodeProps } from "@xyflow/react";
import { ListShell, ListShellItem, BookIcon } from "@anima/standard-templates";
import type { JournalNode } from "./node-types";

function stripHtml(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

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

      <ListShell
        title="Journal"
        icon={<BookIcon size="sm" className="text-muted-foreground" />}
        onClose={onClose}
        headerExtra={
          <span className="font-mono text-micro text-muted-foreground/60 ml-2">
            {entries.length}
          </span>
        }
        actions={[{ id: "new", label: "write +", onClick: onNewEntry }]}
        footer={
          entries.length > 10 ? (
            <div className="px-4 h-8 flex items-center justify-between">
              <span className="font-mono text-micro tracking-wider text-muted-foreground/60">
                +{entries.length - 10} more
              </span>
              <button
                onClick={() => onNavigate("/journal")}
                className="font-mono text-micro tracking-wider text-muted-foreground hover:text-foreground transition-colors"
              >
                view all →
              </button>
            </div>
          ) : undefined
        }
        emptyState={
          entries.length === 0 ? (
            <p className="font-mono text-micro tracking-widest uppercase text-muted-foreground">
              no entries yet
            </p>
          ) : undefined
        }
      >
        {(
          <div className="divide-y divide-accent/20">
            {entries.slice(0, 10).map((entry) => {
              const body = stripHtml(entry.body);
              return (
                <ListShellItem
                  key={entry.id}
                  onClick={() => onNavigate("/journal")}
                >
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-micro tracking-wider uppercase text-muted-foreground group-hover/item:text-accent-foreground/60 transition-colors duration-150">
                        {formatEntryDate(entry.entryDate)}
                      </span>
                      {entry.mood && (
                        <span className="font-mono text-micro text-muted-foreground/60 group-hover/item:text-accent-foreground/50 ml-auto shrink-0 transition-colors duration-150">
                          {entry.mood}
                        </span>
                      )}
                    </div>
                    {entry.title && (
                      <p className="text-detail font-medium text-foreground group-hover/item:text-accent-foreground truncate leading-tight transition-colors duration-150">
                        {entry.title}
                      </p>
                    )}
                    {body && (
                      <p
                        className="text-caption leading-relaxed text-muted-foreground group-hover/item:text-accent-foreground/70 overflow-hidden transition-colors duration-150"
                        style={{
                          display: "-webkit-box",
                          WebkitLineClamp: entry.title ? 1 : 2,
                          WebkitBoxOrient: "vertical",
                        }}
                      >
                        {body}
                      </p>
                    )}
                  </div>
                </ListShellItem>
              );
            })}
          </div>
        )}
      </ListShell>
    </>
  );
}
