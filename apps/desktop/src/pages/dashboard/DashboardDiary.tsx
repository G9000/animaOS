import type { MemoryEpisodeData } from "@anima/api-client";
import { cn } from "@anima/standard-templates";

interface DashboardDiaryProps {
  episodes: MemoryEpisodeData[];
  agentName: string;
  avatarUrl?: string;
  onChat?: (ep: MemoryEpisodeData) => void;
  hideHeader?: boolean;
}

function formatEpisodeDate(dateStr: string, timeStr: string | null): string {
  const d = new Date(timeStr ? `${dateStr}T${timeStr}` : dateStr);
  if (Number.isNaN(d.getTime())) return dateStr;
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: d.getFullYear() !== new Date().getFullYear() ? "numeric" : undefined,
  });
}

export function DashboardDiary({ episodes, agentName, avatarUrl, onChat, hideHeader }: DashboardDiaryProps) {
  if (episodes.length === 0) {
    return (
      <div className="animate-fade-in py-16 text-center">
        <p className="font-mono text-[10px] tracking-[0.22em] uppercase text-muted-foreground/40">
          No entries yet — episodes appear after meaningful conversations.
        </p>
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      {!hideHeader && (
        <div className="flex items-center gap-3 mb-5">
          <span className="font-mono text-[10px] tracking-[0.22em] uppercase text-foreground/70 font-medium">
            {agentName}&apos;s Journal
          </span>
          <div className="flex-1 h-px bg-border" />
        </div>
      )}

      <div className="space-y-3">
        {episodes.map((ep) => (
          <article
            key={ep.id}
            className="group bg-card border border-border hover:border-accent/40 transition-all duration-200"
          >
            <div className="p-5">
              {/* Top row: avatar + name + date + arc + stars */}
              <div className="flex items-center gap-2.5 mb-3">
                {avatarUrl && (
                  <img
                    src={avatarUrl}
                    alt={agentName}
                    className="w-7 h-7 rounded-full object-cover border border-border shrink-0"
                  />
                )}
                <div className="flex items-center gap-2 flex-1 min-w-0 flex-wrap">
                  <span className="text-sm font-semibold text-foreground">{agentName}</span>
                  <span className="text-muted-foreground/60">·</span>
                  <span className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider">
                    {formatEpisodeDate(ep.date, ep.time)}
                  </span>
                  {ep.emotionalArc && (
                    <>
                      <span className="text-muted-foreground/60">·</span>
                      <span className="font-mono text-[10px] text-muted-foreground/80 italic">
                        {ep.emotionalArc}
                      </span>
                    </>
                  )}
                </div>
                <div className="shrink-0 flex gap-0.5">
                  {[1, 2, 3, 4, 5].map((n) => (
                    <span
                      key={n}
                      className={cn(
                        "text-[12px] leading-none",
                        n <= ep.significanceScore ? "text-accent" : "text-muted-foreground/20",
                      )}
                    >★</span>
                  ))}
                </div>
              </div>

              {/* Summary */}
              <p className="text-sm text-foreground leading-relaxed mb-4">
                {ep.summary}
              </p>

              {/* Footer */}
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-x-2 gap-y-1 flex-wrap min-w-0">
                  {ep.topics.slice(0, 4).map((topic) => (
                    <span
                      key={topic}
                      className="font-mono text-[9px] tracking-widest uppercase px-2 py-0.5 bg-secondary border border-border text-foreground/70 cursor-default"
                    >
                      {topic}
                    </span>
                  ))}
                  {ep.topics.length > 4 && (
                    <span className="font-mono text-[9px] text-muted-foreground/50">
                      +{ep.topics.length - 4}
                    </span>
                  )}
                </div>

                {onChat && (
                  <button
                    onClick={() => onChat(ep)}
                    className="shrink-0 font-mono text-[10px] tracking-[0.15em] uppercase px-3 py-1.5 border border-border text-foreground/70 hover:bg-accent hover:text-accent-foreground hover:border-accent transition-all duration-150"
                  >
                    Chat →
                  </button>
                )}
              </div>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
