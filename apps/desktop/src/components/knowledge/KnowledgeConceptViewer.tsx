import ReactMarkdown from "react-markdown";
import type { KnowledgeConcept } from "@anima/api-client";

interface KnowledgeConceptViewerProps {
  concept: KnowledgeConcept | null;
  loading: boolean;
}

export default function KnowledgeConceptViewer({
  concept,
  loading,
}: KnowledgeConceptViewerProps) {
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground/40">
        Loading concept
      </div>
    );
  }

  if (!concept) {
    return (
      <div className="flex h-full items-center justify-center font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground/35">
        Select a concept
      </div>
    );
  }

  return (
    <article className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b border-border px-6 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-muted-foreground/45">
              {concept.conceptType} / {concept.status}
            </p>
            <h1 className="mt-1 truncate text-xl font-semibold text-foreground">
              {concept.title}
            </h1>
            {concept.description && (
              <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
                {concept.description}
              </p>
            )}
          </div>
          <span className="font-mono text-[9px] text-muted-foreground/35">
            {concept.slug}
          </span>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_280px]">
        <div className="min-h-0 overflow-y-auto px-6 py-5">
          <div className="prose prose-invert max-w-none prose-headings:font-semibold prose-p:text-sm prose-p:leading-7 prose-li:text-sm prose-code:text-xs">
            <ReactMarkdown>{concept.bodyMarkdown || "_No body yet._"}</ReactMarkdown>
          </div>
        </div>

        <aside className="min-h-0 overflow-y-auto border-l border-border bg-card/30">
          <div className="border-b border-border px-4 py-3">
            <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-muted-foreground/45">
              Citations
            </p>
            <p className="font-mono text-[9px] text-muted-foreground/30">
              {concept.citations.length} spans
            </p>
          </div>
          <div className="divide-y divide-border/70">
            {concept.citations.length === 0 ? (
              <p className="px-4 py-5 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground/35">
                No citations
              </p>
            ) : (
              concept.citations.map((citation) => (
                <div key={citation.id} className="px-4 py-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-[9px] uppercase tracking-[0.14em] text-primary/80">
                      {citation.citationLabel || `S${citation.spanId}`}
                    </span>
                    <span className="font-mono text-[9px] text-muted-foreground/30">
                      #{citation.sourceId}
                    </span>
                  </div>
                  <p className="mt-2 line-clamp-5 text-xs leading-5 text-foreground/80">
                    {citation.quoteText || citation.contentText}
                  </p>
                  <p className="mt-2 truncate font-mono text-[9px] text-muted-foreground/35">
                    {citation.sourceTitle || citation.sourceUri}
                  </p>
                </div>
              ))
            )}
          </div>
        </aside>
      </div>
    </article>
  );
}
