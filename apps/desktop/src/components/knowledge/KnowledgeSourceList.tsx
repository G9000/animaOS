import type { KnowledgeSource } from "@anima/api-client";
import { Icons } from "../database/Icons";

interface KnowledgeSourceListProps {
  sources: KnowledgeSource[];
  selectedSourceId: number | null;
  loading: boolean;
  onSelect: (sourceId: number) => void;
  onCompile: (sourceId: number) => void;
}

export default function KnowledgeSourceList({
  sources,
  selectedSourceId,
  loading,
  onSelect,
  onCompile,
}: KnowledgeSourceListProps) {
  return (
    <section className="flex h-full min-h-0 flex-col border-r border-border bg-card/35">
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
        <div>
          <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-muted-foreground/45">
            Sources
          </p>
          <p className="font-mono text-[9px] text-muted-foreground/30">
            {sources.length} indexed
          </p>
        </div>
        {loading && (
          <span className="font-mono text-[9px] uppercase tracking-[0.18em] text-muted-foreground/40">
            Loading
          </span>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {sources.length === 0 && !loading ? (
          <div className="px-4 py-6 font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground/35">
            No sources
          </div>
        ) : (
          sources.map((source) => (
            <div
              key={source.id}
              className={`group border-b border-border/70 px-4 py-3 transition-colors ${
                selectedSourceId === source.id
                  ? "bg-primary/[0.08]"
                  : "hover:bg-foreground/[0.03]"
              }`}
            >
              <button
                type="button"
                onClick={() => onSelect(source.id)}
                className="w-full text-left"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-foreground">
                      {source.title || source.sourceUri}
                    </p>
                    <p className="mt-1 truncate font-mono text-[9px] text-muted-foreground/35">
                      {source.kind} / {source.status}
                    </p>
                  </div>
                  <span className="shrink-0 font-mono text-[9px] text-muted-foreground/30">
                    #{source.id}
                  </span>
                </div>
              </button>
              <div className="mt-2 flex justify-end opacity-0 transition-opacity group-hover:opacity-100">
                <button
                  type="button"
                  onClick={() => onCompile(source.id)}
                  className="inline-flex items-center gap-1 border border-border px-2 py-1 font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground/60 hover:border-primary/40 hover:text-primary"
                  title="Queue compile"
                >
                  <Icons.Refresh />
                  Compile
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
