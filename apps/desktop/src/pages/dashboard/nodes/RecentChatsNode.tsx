import { NodeResizer, type NodeProps } from "@xyflow/react";
import { ListShell, ListShellItem, ChatIcon, ArrowRightIcon } from "@anima/standard-templates";
import type { RecentChatsNode } from "./node-types";

function formatLastReply(iso: string | null): string {
  if (!iso) return "no messages";
  const d = new Date(iso);
  const today = new Date();
  const isToday = d.toDateString() === today.toDateString();
  const time = d.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
  if (isToday) return time;
  return (
    d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
    "  " +
    time
  );
}

const MAX_THREADS = 10;

export function RecentChatsNode({ data }: NodeProps<RecentChatsNode>) {
  const { threads, onPreviewThread, onNewChat, onNavigate, onClose } = data;

  const recent = [...threads]
    .sort((a, b) => {
      const ta = a.lastMessageAt ?? a.createdAt ?? "";
      const tb = b.lastMessageAt ?? b.createdAt ?? "";
      return tb.localeCompare(ta);
    })
    .slice(0, MAX_THREADS);

  return (
    <>
      <NodeResizer
        minWidth={220}
        minHeight={160}
        maxWidth={480}
        maxHeight={700}
        lineStyle={{
          borderColor: "var(--border)",
          borderWidth: 1,
          opacity: 0.35,
        }}
        handleStyle={{
          width: 8,
          height: 8,
          borderRadius: 2,
          border: "1px solid var(--border)",
          background: "var(--background)",
          opacity: 0.55,
        }}
      />

      <ListShell
        title="Chats"
        icon={<ChatIcon size="sm" className="text-muted-foreground" />}
        onClose={onClose}
        headerExtra={
          <span className="font-mono text-micro text-muted-foreground/60 ml-2">
            {recent.length}
          </span>
        }
        actions={[{ id: "new", label: "new +", onClick: onNewChat }]}
        topActions={
          <button
            onClick={() => onNavigate("/chat")}
            className="px-2 flex items-center text-muted-foreground/50 hover:text-foreground hover:bg-accent transition-colors"
            aria-label="All chats"
          >
            <ArrowRightIcon size="sm" className="-rotate-45" />
          </button>
        }
        emptyState={
          recent.length === 0 ? (
            <div className="text-center space-y-2">
              <p className="font-mono text-micro tracking-widest uppercase text-muted-foreground">
                no conversations yet
              </p>
            </div>
          ) : undefined
        }
      >
        {
          <div className="divide-y divide-accent/20">
            {recent.map((thread) => (
              <ListShellItem
                key={thread.id}
                onClick={() => onPreviewThread(thread.id)}
              >
                <div className="relative pr-4">
                  <p className="font-mono text-micro uppercase text-muted-foreground group-hover/item:text-accent-foreground/60 tracking-wider mt-0.5 transition-colors duration-150">
                    {formatLastReply(thread.lastMessageAt ?? thread.createdAt)}
                  </p>
                  <p className="text-detail font-medium text-foreground group-hover/item:text-accent-foreground truncate leading-tight transition-colors duration-150">
                    {thread.title ?? "Untitled conversation"}
                  </p>
                  <span className="absolute right-0 top-1/2 -translate-y-1/2 opacity-0 group-hover/item:opacity-100 text-accent-foreground/70 transition-opacity duration-150 -rotate-45">
                    <ArrowRightIcon size="sm" />
                  </span>
                </div>
              </ListShellItem>
            ))}
          </div>
        }
      </ListShell>
    </>
  );
}
