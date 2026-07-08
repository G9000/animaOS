import { useEffect, useMemo, useState } from "react";
import type {
  KnowledgeConcept,
  KnowledgeConceptSummary,
  KnowledgeLintFinding,
  KnowledgeSource,
  KnowledgeSourceResponse,
} from "@anima/api-client";
import { Icons } from "../../components/database/Icons";
import KnowledgeConceptViewer from "../../components/knowledge/KnowledgeConceptViewer";
import KnowledgeSourceList from "../../components/knowledge/KnowledgeSourceList";
import { useAuth } from "../../context/AuthContext";
import { api } from "../../lib/api";

export default function KnowledgeLibrary() {
  const { user } = useAuth();
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [concepts, setConcepts] = useState<KnowledgeConceptSummary[]>([]);
  const [selectedSource, setSelectedSource] = useState<KnowledgeSourceResponse | null>(null);
  const [selectedConceptId, setSelectedConceptId] = useState<number | null>(null);
  const [selectedConcept, setSelectedConcept] = useState<KnowledgeConcept | null>(null);
  const [query, setQuery] = useState("");
  const [lintFindings, setLintFindings] = useState<KnowledgeLintFinding[]>([]);
  const [loading, setLoading] = useState(false);
  const [conceptLoading, setConceptLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const visibleConcepts = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return concepts;
    return concepts.filter((concept) =>
      [concept.title, concept.description, concept.slug, concept.conceptType]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle)),
    );
  }, [concepts, query]);

  useEffect(() => {
    if (user?.id == null) return;
    void loadLibrary();
  }, [user?.id]);

  useEffect(() => {
    if (user?.id == null || selectedConceptId == null) {
      setSelectedConcept(null);
      return;
    }
    let active = true;
    setConceptLoading(true);
    api.knowledge
      .readConcept(user.id, selectedConceptId)
      .then((concept) => {
        if (active) setSelectedConcept(concept);
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : "Failed to load concept");
      })
      .finally(() => {
        if (active) setConceptLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedConceptId, user?.id]);

  async function loadLibrary() {
    if (user?.id == null) return;
    setLoading(true);
    setError(null);
    try {
      const [sourcePayload, conceptPayload] = await Promise.all([
        api.knowledge.listSources(user.id, 100),
        api.knowledge.listConcepts(user.id, 100),
      ]);
      setSources(sourcePayload.sources);
      setConcepts(conceptPayload.concepts);
      if (selectedConceptId == null && conceptPayload.concepts[0]) {
        setSelectedConceptId(conceptPayload.concepts[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load knowledge library");
    } finally {
      setLoading(false);
    }
  }

  async function selectSource(sourceId: number) {
    if (user?.id == null) return;
    setError(null);
    try {
      const source = await api.knowledge.readSource(user.id, sourceId);
      setSelectedSource(source);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load source");
    }
  }

  async function compileSource(sourceId: number) {
    if (user?.id == null) return;
    setMessage(null);
    setError(null);
    try {
      const response = await api.knowledge.compileSource(user.id, sourceId);
      setMessage(`Compile run ${response.compileRun.id} ${response.compileRun.status}`);
      await loadLibrary();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to compile source");
    }
  }

  async function runLint() {
    if (user?.id == null) return;
    setMessage(null);
    setError(null);
    try {
      const response = await api.knowledge.runLint(user.id, {
        sourceId: selectedSource?.source.id,
        conceptId: selectedConceptId ?? undefined,
      });
      setLintFindings(response.findings);
      setMessage(`${response.findings.length} lint finding(s)`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run lint");
    }
  }

  async function exportBundle() {
    if (user?.id == null) return;
    setMessage(null);
    setError(null);
    try {
      const blob = await api.knowledge.exportBundle(user.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "knowledge-bundle.zip";
      anchor.click();
      URL.revokeObjectURL(url);
      setMessage("Exported OKF bundle");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to export OKF bundle");
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden pt-16">
      <header className="shrink-0 border-b border-border bg-card/45 px-5 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted-foreground/45">
              Knowledge Library
            </p>
            <p className="mt-1 font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground/30">
              {sources.length} sources / {concepts.length} concepts
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="relative">
              <span className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground/35">
                <Icons.Search />
              </span>
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Filter concepts"
                className="h-9 w-56 border border-border bg-input pl-8 pr-3 font-mono text-[10px] text-foreground outline-none placeholder:text-muted-foreground/30 focus:border-primary/40"
              />
            </div>
            <button
              type="button"
              onClick={runLint}
              className="inline-flex h-9 items-center gap-2 border border-border px-3 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground hover:border-primary/40 hover:text-primary"
              title="Run knowledge lint"
            >
              <Icons.Warning />
              Lint
            </button>
            <button
              type="button"
              onClick={exportBundle}
              className="inline-flex h-9 items-center gap-2 border border-border px-3 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground hover:border-primary/40 hover:text-primary"
              title="Export OKF bundle"
            >
              <Icons.Download />
              Export
            </button>
            <button
              type="button"
              onClick={loadLibrary}
              className="inline-flex h-9 items-center gap-2 border border-border px-3 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground hover:border-primary/40 hover:text-primary"
              title="Refresh"
            >
              <Icons.Refresh />
              Refresh
            </button>
          </div>
        </div>
        {(error || message) && (
          <div className="mt-3 font-mono text-[10px] uppercase tracking-[0.12em]">
            {error ? (
              <span className="text-destructive">{error}</span>
            ) : (
              <span className="text-muted-foreground/50">{message}</span>
            )}
          </div>
        )}
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-[280px_320px_minmax(0,1fr)]">
        <KnowledgeSourceList
          sources={sources}
          selectedSourceId={selectedSource?.source.id ?? null}
          loading={loading}
          onSelect={selectSource}
          onCompile={compileSource}
        />

        <section className="flex min-h-0 flex-col border-r border-border bg-card/20">
          <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
            <div>
              <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-muted-foreground/45">
                Concepts
              </p>
              <p className="font-mono text-[9px] text-muted-foreground/30">
                {visibleConcepts.length} visible
              </p>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {visibleConcepts.length === 0 && !loading ? (
              <div className="px-4 py-6 font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground/35">
                No concepts
              </div>
            ) : (
              visibleConcepts.map((concept) => (
                <button
                  key={concept.id}
                  type="button"
                  onClick={() => setSelectedConceptId(concept.id)}
                  className={`block w-full border-b border-border/70 px-4 py-3 text-left transition-colors ${
                    selectedConceptId === concept.id
                      ? "bg-primary/[0.08]"
                      : "hover:bg-foreground/[0.03]"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-foreground">
                        {concept.title}
                      </p>
                      <p className="mt-1 truncate font-mono text-[9px] text-muted-foreground/35">
                        {concept.conceptType} / {concept.slug}
                      </p>
                    </div>
                    <span className="font-mono text-[9px] text-muted-foreground/30">
                      #{concept.id}
                    </span>
                  </div>
                </button>
              ))
            )}
          </div>

          <div className="max-h-48 shrink-0 overflow-y-auto border-t border-border">
            <div className="px-4 py-3">
              <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-muted-foreground/45">
                Lint
              </p>
              {lintFindings.length === 0 ? (
                <p className="mt-2 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground/35">
                  No findings
                </p>
              ) : (
                <div className="mt-2 space-y-2">
                  {lintFindings.map((finding, index) => (
                    <div key={`${finding.code}-${index}`} className="border-l border-border pl-2">
                      <p className="font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground/45">
                        {finding.severity} / {finding.code}
                      </p>
                      <p className="mt-1 text-xs leading-5 text-foreground/75">
                        {finding.message}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </section>

        <KnowledgeConceptViewer concept={selectedConcept} loading={conceptLoading} />
      </div>
    </div>
  );
}
