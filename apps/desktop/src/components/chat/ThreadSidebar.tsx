import type { Thread, ThreadContextStats } from "@anima/api-client";
import { cn, PlusIcon, ChevronLeftIcon, XIcon } from "@anima/standard-templates";

interface ThreadSidebarProps {
  threads: Thread[];
  currentThreadId: number | null;
  searchQuery: string;
  onSearchChange: (query: string) => void;
  onSelectThread: (threadId: number) => void;
  onNewThread: () => void;
  onDeleteThread: (threadId: number) => void;
  onToggleSidebar: () => void;
  contextStats?: ThreadContextStats | null;
}

function relativeTime(date: Date): string {
  const diff = Date.now() - date.getTime();
  const mins = Math.floor(diff / 60_000);
  const hours = Math.floor(diff / 3_600_000);
  const days = Math.floor(diff / 86_400_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days === 1) return "yesterday";
  if (days < 7) return `${days}d ago`;
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

function formatThreadTitle(thread: Thread): string {
  const title = thread.title?.trim();
  if (title) return title;
  const raw = thread.lastMessageAt ?? thread.createdAt;
  if (!raw) return "New chat";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return "New chat";
  return date.toLocaleString([], {
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
  contextStats,
}: ThreadSidebarProps) {
  const filteredThreads = threads.filter((t) =>
    searchQuery
      ? formatThreadTitle(t).toLowerCase().includes(searchQuery.toLowerCase())
      : true,
  );

  const pct = contextStats?.pct ?? null;
  const barColor = pct == null
    ? "bg-foreground/20"
    : pct >= 80 ? "bg-destructive/70"
    : pct >= 60 ? "bg-accent/80"
    : "bg-foreground/25";

  return (
    <div className="w-64 flex-shrink-0 border-r border-foreground/[0.08] flex flex-col bg-background/25 backdrop-blur-[40px] shadow-[0_20px_50px_-12px_rgba(0,0,0,0.28)] pt-14">

      {/* Header */}
      <div className="flex items-center gap-1.5 px-3 pt-4 pb-3 flex-shrink-0">
        <button
          onClick={onToggleSidebar}
          title="Collapse sidebar"
          className="size-7 flex items-center justify-center text-foreground/30 hover:text-foreground hover:bg-foreground/[0.06] rounded-sm transition-colors shrink-0"
        >
          <ChevronLeftIcon size="sm" />
        </button>
        <span className="flex-1 font-mono text-[9px] tracking-[0.22em] uppercase text-foreground/30 select-none text-center">
          Chats
        </span>
        <button
          onClick={onNewThread}
          title="New chat"
          className="size-7 flex items-center justify-center text-foreground/30 hover:text-foreground hover:bg-foreground/[0.06] rounded-sm transition-colors shrink-0"
        >
          <PlusIcon size="sm" />
        </button>
      </div>

      {/* Search */}
      <div className="px-3 pb-3 flex-shrink-0">
        <div className={cn(
          "flex items-center gap-2 h-8 px-2.5 rounded-sm transition-colors",
          "bg-foreground/[0.04] border border-foreground/[0.06]",
          "focus-within:border-foreground/[0.14] focus-within:bg-foreground/[0.06]",
        )}>
          <svg width="11" height="11" viewBox="0 0 11 11" fill="none" className="text-foreground/25 shrink-0">
            <circle cx="4.5" cy="4.5" r="3.5" stroke="currentColor" strokeWidth="1.3" />
            <path d="M7.5 7.5l2.5 2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
          </svg>
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search..."
            className="flex-1 min-w-0 bg-transparent text-xs text-foreground placeholder:text-foreground/25 outline-none font-mono"
          />
          {searchQuery && (
            <button
              onClick={() => onSearchChange("")}
              className="shrink-0 text-foreground/25 hover:text-foreground transition-colors"
            >
              <XIcon size="sm" />
            </button>
          )}
        </div>
      </div>

      {/* Divider */}
      <div className="h-px bg-foreground/[0.06] mx-3 flex-shrink-0" />

      {/* Thread list */}
      <div className="flex-1 overflow-y-auto px-2 py-2 space-y-px">
        {filteredThreads.length === 0 ? (
          <div className="py-12 font-mono text-[9px] text-foreground/20 tracking-[0.2em] uppercase text-center">
            {searchQuery ? "No match" : "No chats yet"}
          </div>
        ) : (
          filteredThreads.map((thread) => {
            const isActive = thread.id === currentThreadId;
            const raw = thread.lastMessageAt ?? thread.createdAt;
            const ts = raw ? new Date(raw) : null;
            const timeStr = ts && !Number.isNaN(ts.getTime()) ? relativeTime(ts) : null;

            return (
              <div
                key={thread.id}
                className={cn(
                  "group/thread relative flex items-center rounded-sm transition-colors",
                  isActive
                    ? "bg-foreground/[0.08]"
                    : "hover:bg-foreground/[0.04]",
                )}
              >
                {isActive && (
                  <div className="absolute left-0 inset-y-2 w-[2px] bg-accent rounded-full" />
                )}

                <button
                  onClick={() => onSelectThread(thread.id)}
                  className="flex-1 text-left pl-4 pr-2 py-2.5 min-w-0"
                >
                  <div className={cn(
                    "text-[13px] truncate leading-snug",
                    isActive
                      ? "text-foreground font-medium"
                      : "text-foreground/55 group-hover/thread:text-foreground/90",
                  )}>
                    {formatThreadTitle(thread)}
                  </div>
                  {timeStr && (
                    <div className="font-mono text-[9px] text-foreground/25 mt-0.5 tracking-wide">
                      {timeStr}
                    </div>
                  )}
                </button>

                <button
                  onClick={() => onDeleteThread(thread.id)}
                  title="Delete"
                  className="opacity-0 group-hover/thread:opacity-100 shrink-0 mr-1.5 size-6 flex items-center justify-center text-foreground/20 hover:text-destructive transition-all rounded-sm"
                >
                  <XIcon size="sm" />
                </button>
              </div>
            );
          })
        )}
      </div>

      {/* Context usage footer */}
      {contextStats && (
        <div className="flex-shrink-0 border-t border-foreground/[0.06] px-3 py-2.5">
          <div className="flex items-center justify-between mb-1.5">
            <span className="font-mono text-[8px] tracking-[0.2em] uppercase text-foreground/25 select-none">
              Context
            </span>
            <span className="font-mono text-[8px] text-foreground/30 select-none">
              {pct != null
                ? `${pct}%`
                : `${contextStats.usedTokens.toLocaleString()}t`}
              {contextStats.compactionCount > 0 && (
                <span className="ml-1.5 text-foreground/20">
                  · {contextStats.compactionCount}× compact
                </span>
              )}
            </span>
          </div>
          <div className="h-[3px] bg-foreground/[0.06] overflow-hidden">
            <div
              className={cn("h-full transition-all duration-700", barColor)}
              style={{ width: `${Math.min(pct ?? 0, 100)}%` }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
