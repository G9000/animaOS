import type { RetrievalTrace } from "./types";
import { cn } from "../utils/cn";

export interface RetrievalPanelProps {
  retrieval: RetrievalTrace;
  className?: string;
  maxFragments?: number;
}

export function RetrievalPanel({
  retrieval,
  className,
  maxFragments = 3,
}: RetrievalPanelProps) {
  const fragments = retrieval.contextFragments.slice(0, maxFragments);
  const hiddenCount = Math.max(
    0,
    retrieval.contextFragments.length - fragments.length,
  );
  const stats = retrieval.stats;

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex flex-wrap items-center gap-1.5 font-mono text-[9px] tracking-wider text-muted-foreground/55">
        <TraceChip
          label="RETRIEVER"
          value={retrieval.retriever}
          accent="text-emerald-400/70"
        />
        <TraceChip
          label="HITS"
          value={formatHitCount(retrieval)}
          accent="text-emerald-400/70"
        />
        {stats?.retrievalMs != null && (
          <TraceChip
            label="LATENCY"
            value={formatMilliseconds(stats.retrievalMs)}
            accent="text-blue-400/70"
          />
        )}
        {stats?.triggeredBy && (
          <TraceChip
            label="CUTOFF"
            value={stats.triggeredBy}
            accent="text-yellow-400/70"
          />
        )}
      </div>

      {fragments.length > 0 ? (
        <div className="space-y-2">
          {fragments.map((fragment) => (
            <div
              key={`${fragment.rank}-${fragment.memoryItemId}`}
              className="border border-border/60 bg-background/25 px-2.5 py-2"
            >
              <div className="flex flex-wrap items-center gap-2 font-mono text-[9px] tracking-wider text-muted-foreground/55">
                <span className="text-emerald-400/70">#{fragment.rank}</span>
                <span>item:{fragment.memoryItemId}</span>
                {fragment.category && (
                  <span className="text-blue-400/60">{fragment.category}</span>
                )}
                {fragment.score != null && (
                  <span className="text-yellow-400/60">
                    {formatScore(fragment.score)}
                  </span>
                )}
              </div>
              <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground/85 whitespace-pre-wrap break-words">
                {fragment.text}
              </p>
            </div>
          ))}
        </div>
      ) : (
        <div className="border border-border/60 bg-background/25 px-2.5 py-2 font-mono text-[10px] text-muted-foreground/45 tracking-wider">
          No context fragments returned.
        </div>
      )}

      {hiddenCount > 0 && (
        <div className="font-mono text-[9px] tracking-wider text-muted-foreground/35">
          +{hiddenCount} more fragment{hiddenCount === 1 ? "" : "s"}
        </div>
      )}
    </div>
  );
}

function TraceChip({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent: string;
}) {
  return (
    <span className="inline-flex items-center gap-1 border border-border/50 bg-background/25 px-1.5 py-1">
      <span className={accent}>{label}</span>
      <span>{value}</span>
    </span>
  );
}

function formatHitCount(retrieval: RetrievalTrace): string {
  const returned =
    retrieval.stats?.returned ?? retrieval.contextFragments.length;
  const total = retrieval.stats?.totalConsidered;
  if (total != null && total > 0) {
    return `${returned}/${total}`;
  }
  return String(returned);
}

function formatMilliseconds(value: number): string {
  if (value >= 100) {
    return `${Math.round(value)}ms`;
  }
  return `${value.toFixed(1)}ms`;
}

function formatScore(score: number): string {
  return `${Math.round(score * 100)}%`;
}
