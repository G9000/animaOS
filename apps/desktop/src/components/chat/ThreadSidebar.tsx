import type { Thread } from "@anima/api-client";

interface ThreadSidebarProps {
  threads: Thread[];
  currentThreadId: number | null;
  searchQuery: string;
  onSearchChange: (query: string) => void;
  onSelectThread: (threadId: number) => void;
  onNewThread: () => void;
  onDeleteThread: (threadId: number) => void;
  onToggleSidebar: () => void;
}

function getThreadTimestamp(thread: Thread): Date | null {
  const raw = thread.lastMessageAt ?? thread.createdAt;
  if (!raw) return null;
  return new Date(raw);
}

function formatThreadTitle(thread: Thread): string {
  const title = thread.title?.trim();
  if (title) return title;

  const timestamp = getThreadTimestamp(thread);
  if (!timestamp || Number.isNaN(timestamp.getTime())) return "Untitled chat";

  return timestamp.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function ThreadSidebar({
  threads,
  currentThreadId,
  searchQuery,
  onSearchChange,
  onSelectThread,
  onNewThread,
  onDeleteThread,
  onToggleSidebar,
}: ThreadSidebarProps) {
  const filteredThreads = threads.filter((t) =>
    searchQuery
      ? formatThreadTitle(t).toLowerCase().includes(searchQuery.toLowerCase())
      : true,
  );

  return (
    <div className="w-72 flex-shrink-0 border-l border-border flex flex-col bg-sidebar">
      {/* Header */}
      <div className="px-4 py-2.5 border-b border-border flex items-center justify-between gap-2 flex-shrink-0">
        <button
          onClick={onToggleSidebar}
          className="font-mono text-[9px] tracking-widest text-muted-foreground/30 hover:text-muted-foreground transition-colors"
          title="Minimize"
        >
          ▶
        </button>
        <button
          onClick={onNewThread}
          className="font-mono text-[9px] tracking-widest text-muted-foreground/40 hover:text-primary transition-colors"
          title="New chat"
        >
          + NEW
        </button>
      </div>

      {/* Thread list */}
      <div className="flex-1 overflow-y-auto">
        {filteredThreads.length === 0 ? (
          <div className="px-4 py-6 font-mono text-[9px] text-muted-foreground/25 tracking-widest text-center">
            {searchQuery ? "NO MATCH" : "NO CONVERSATIONS"}
          </div>
        ) : (
          filteredThreads.map((thread) => {
            const isActive = thread.id === currentThreadId;
            return (
              <div
                key={thread.id}
                className={`group/thread flex items-center transition-colors border-b border-border/20 ${
                  isActive
                    ? "bg-primary/10 text-foreground"
                    : "text-muted-foreground hover:bg-card/50 hover:text-foreground"
                }`}
              >
                <button
                  onClick={() => onSelectThread(thread.id)}
                  className="flex-1 text-left px-4 py-3 min-w-0"
                >
                  <div className="text-sm truncate leading-snug">
                    {formatThreadTitle(thread)}
                  </div>
                </button>
                <button
                  onClick={() => onDeleteThread(thread.id)}
                  className="opacity-0 group-hover/thread:opacity-100 shrink-0 px-3 py-3 font-mono text-[9px] text-muted-foreground/40 hover:text-destructive transition-all"
                  title="Delete thread"
                >
                  ✕
                </button>
              </div>
            );
          })
        )}
      </div>

      {/* Search */}
      <div className="border-t border-border bg-card/40 px-4 py-3 flex-shrink-0 flex items-center gap-2.5">
        <span className="font-mono text-[10px] text-primary/40 tracking-wider select-none shrink-0">
          /
        </span>
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="search conversations..."
          className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground/35 outline-none py-0.5"
        />
        {searchQuery && (
          <button
            onClick={() => onSearchChange("")}
            className="font-mono text-[9px] text-muted-foreground/40 hover:text-foreground transition-colors shrink-0"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
