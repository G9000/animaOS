import type { MemoryEpisodeData } from "@anima/api-client";

interface DashboardDiaryProps {
  episodes: MemoryEpisodeData[];
  agentName: string;
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

export function DashboardDiary({ episodes, agentName }: DashboardDiaryProps) {
  if (episodes.length === 0) {
    return (
      <div className="animate-fade-in">
        <div className="flex items-center gap-2 mb-3">
          <span className="font-mono text-[9px] tracking-[0.22em] uppercase text-muted-foreground/40">
            {agentName}&apos;s Diary
          </span>
          <div className="flex-1 h-px bg-border/40" />
        </div>
        <p className="font-mono text-[10px] text-muted-foreground/30 tracking-wider">
          NO ENTRIES YET. EPISODES APPEAR AFTER MEANINGFUL CONVERSATIONS.
        </p>
      </div>
    );
  }

  return (
    <div className="animate-fade-in space-y-3">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[9px] tracking-[0.22em] uppercase text-muted-foreground/40">
          {agentName}&apos;s Diary
        </span>
        <div className="flex-1 h-px bg-border/40" />
      </div>

      <div className="space-y-2">
        {episodes.map((ep) => (
          <div
            key={ep.id}
            className="group bg-card/50 border border-border/60 hover:border-border transition-colors p-3"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm text-foreground leading-relaxed">
                  {ep.summary}
                </p>
                <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                  <span className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
                    {formatEpisodeDate(ep.date, ep.time)}
                  </span>
                  {ep.emotionalArc && (
                    <>
                      <span className="text-border">·</span>
                      <span className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
                        {ep.emotionalArc}
                      </span>
                    </>
                  )}
                  {ep.turnCount != null && ep.turnCount > 0 && (
                    <>
                      <span className="text-border">·</span>
                      <span className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
                        {ep.turnCount} turns
                      </span>
                    </>
                  )}
                </div>
                {ep.topics.length > 0 && (
                  <div className="flex gap-1 mt-2 flex-wrap">
                    {ep.topics.slice(0, 4).map((topic) => (
                      <span
                        key={topic}
                        className="font-mono text-[8px] px-1.5 py-0.5 bg-input border border-border tracking-wider text-muted-foreground/50"
                      >
                        {topic.toUpperCase()}
                      </span>
                    ))}
                    {ep.topics.length > 4 && (
                      <span className="font-mono text-[8px] px-1.5 py-0.5 tracking-wider text-muted-foreground/30">
                        +{ep.topics.length - 4}
                      </span>
                    )}
                  </div>
                )}
              </div>
              <div className="shrink-0 flex gap-px pt-0.5">
                {[1, 2, 3, 4, 5].map((n) => (
                  <div
                    key={n}
                    className={`w-1 h-3 ${
                      n <= ep.significanceScore
                        ? "bg-primary/50"
                        : "bg-border/40"
                    }`}
                  />
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
