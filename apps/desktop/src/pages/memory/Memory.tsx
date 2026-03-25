import { useState, useEffect } from "react";
import { useAuth } from "../../context/AuthContext";
import type { MemoryItemData, MemoryEpisodeData, MemoryOverviewData, MemorySearchResult } from "@anima/api-client";
import { api } from "../../lib/api";

type Tab = "facts" | "preferences" | "goals" | "relationships" | "episodes";

const TABS: { key: Tab; label: string }[] = [
  { key: "facts", label: "FACTS" },
  { key: "preferences", label: "PREFS" },
  { key: "goals", label: "GOALS" },
  { key: "relationships", label: "RELATIONS" },
  { key: "episodes", label: "EPISODES" },
];

const IMPORTANCE_LABELS = ["", "Trivial", "Minor", "Notable", "Significant", "Core"];

export default function Memory() {
  const { user } = useAuth();
  const [tab, setTab] = useState<Tab>("facts");
  const [overview, setOverview] = useState<MemoryOverviewData | null>(null);
  const [items, setItems] = useState<MemoryItemData[]>([]);
  const [episodes, setEpisodes] = useState<MemoryEpisodeData[]>([]);
  const [loading, setLoading] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editContent, setEditContent] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [newContent, setNewContent] = useState("");
  const [newImportance, setNewImportance] = useState(3);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<MemorySearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    if (user?.id == null) return;
    api.memory.overview(user.id).then(setOverview).catch(console.error);
  }, [user?.id]);

  useEffect(() => {
    if (user?.id == null) return;
    loadTab();
  }, [user?.id, tab]);

  const loadTab = async () => {
    if (user?.id == null) return;
    setLoading(true);
    try {
      if (tab === "episodes") {
        const data = await api.memory.listEpisodes(user.id);
        setEpisodes(data);
      } else {
        const data = await api.memory.listItems(user.id, tab === "facts" ? "fact" : tab === "preferences" ? "preference" : tab === "goals" ? "goal" : "relationship");
        setItems(data);
      }
    } catch (err) {
      console.error("Failed to load memory:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    if (user?.id == null || !newContent.trim()) return;
    const category =
      tab === "facts" ? "fact" :
      tab === "preferences" ? "preference" :
      tab === "goals" ? "goal" : "relationship";
    try {
      await api.memory.createItem(user.id, {
        content: newContent.trim(),
        category,
        importance: newImportance,
      });
      setNewContent("");
      setNewImportance(3);
      setShowCreate(false);
      loadTab();
      api.memory.overview(user.id).then(setOverview).catch(() => {});
    } catch (err: any) {
      console.error("Failed to create:", err);
    }
  };

  const startEdit = (item: MemoryItemData) => {
    setEditingId(item.id);
    setEditContent(item.content);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditContent("");
  };

  const saveEdit = async (item: MemoryItemData) => {
    if (user?.id == null || !editContent.trim()) return;
    try {
      await api.memory.updateItem(user.id, item.id, {
        content: editContent.trim(),
      });
      setEditingId(null);
      loadTab();
    } catch (err) {
      console.error("Failed to update:", err);
    }
  };

  const deleteItem = async (itemId: number) => {
    if (user?.id == null || !confirm("Delete this memory?")) return;
    try {
      await api.memory.deleteItem(user.id, itemId);
      loadTab();
      api.memory.overview(user.id).then(setOverview).catch(() => {});
    } catch (err) {
      console.error("Failed to delete:", err);
    }
  };

  const handleSearch = async () => {
    if (user?.id == null || !searchQuery.trim()) return;
    setSearching(true);
    try {
      const res = await api.memory.search(user.id, searchQuery.trim());
      setSearchResults(res.results);
    } catch {
      setSearchResults([]);
    } finally {
      setSearching(false);
    }
  };

  const clearSearch = () => {
    setSearchQuery("");
    setSearchResults(null);
  };

  const categoryForTab = (t: Tab): string =>
    t === "facts" ? "fact" : t === "preferences" ? "preference" : t === "goals" ? "goal" : "relationship";

  const countForTab = (t: Tab): number => {
    if (!overview) return 0;
    switch (t) {
      case "facts": return overview.factCount;
      case "preferences": return overview.preferenceCount;
      case "goals": return overview.goalCount;
      case "relationships": return overview.relationshipCount;
      case "episodes": return overview.episodeCount;
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-5 py-2.5 border-b border-border bg-card/40">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="font-mono text-[10px] text-muted-foreground tracking-wider">
              MEMORY
            </span>
            {overview && (
              <>
                <div className="w-px h-3 bg-border" />
                <span className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
                  {overview.totalItems} ITEMS
                </span>
              </>
            )}
          </div>
          <div className="flex items-center gap-3">
            {overview?.currentFocus && (
              <div className="font-mono text-[9px] text-muted-foreground/50">
                <span className="text-muted-foreground/30 tracking-wider mr-1.5">FOCUS:</span>
                {overview.currentFocus}
              </div>
            )}
            <form
              onSubmit={(e) => { e.preventDefault(); handleSearch(); }}
              className="flex items-center gap-1.5"
            >
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value);
                  if (!e.target.value.trim()) clearSearch();
                }}
                placeholder="Search..."
                className="w-32 bg-input border border-border px-2 py-0.5 font-mono text-[10px] text-foreground placeholder:text-muted-foreground/20 outline-none focus:border-primary/40 focus:w-44 transition-all"
              />
              {searchResults !== null && (
                <button
                  type="button"
                  onClick={clearSearch}
                  className="font-mono text-[9px] text-muted-foreground/30 hover:text-muted-foreground tracking-wider"
                >
                  CLR
                </button>
              )}
            </form>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="px-5 py-1.5 border-b border-border flex gap-px">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => { setTab(t.key); setShowCreate(false); setEditingId(null); }}
            className={`font-mono text-[9px] px-2.5 py-1.5 tracking-wider transition-colors ${
              tab === t.key
                ? "bg-primary/[0.08] text-primary border-b-2 border-primary"
                : "text-muted-foreground/50 hover:text-muted-foreground"
            }`}
          >
            {t.label}
            <span className="ml-1 text-muted-foreground/30">
              {countForTab(t.key)}
            </span>
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-5 py-4">
        {/* Search results */}
        {searchResults !== null && (
          <div className="space-y-1 max-w-2xl mb-6">
            <div className="flex items-center justify-between mb-3">
              <span className="font-mono text-[9px] text-muted-foreground/50 tracking-wider">
                {searching ? "SEARCHING..." : `${searchResults.length} RESULT${searchResults.length !== 1 ? "S" : ""} // "${searchQuery}"`}
              </span>
              <button
                onClick={clearSearch}
                className="font-mono text-[9px] text-muted-foreground/30 hover:text-muted-foreground tracking-wider"
              >
                CLEAR
              </button>
            </div>
            {searchResults.map((r) => (
              <div
                key={`${r.type}-${r.id}`}
                className="bg-card border-l-2 border-primary/20 px-4 py-2.5"
              >
                <div className="flex items-start justify-between gap-3">
                  <p className="text-sm text-foreground leading-relaxed flex-1">
                    {r.content}
                  </p>
                  <span className="font-mono text-[8px] px-1.5 py-0.5 bg-input border border-border tracking-wider text-muted-foreground/50">
                    {r.type === "episode" ? "EPISODE" : r.category?.toUpperCase()}
                  </span>
                </div>
              </div>
            ))}
            {searchResults.length === 0 && !searching && (
              <p className="font-mono text-[10px] text-muted-foreground/40 tracking-wider">NO MATCHES</p>
            )}
          </div>
        )}

        {loading && (
          <div className="font-mono text-[10px] text-muted-foreground/40 animate-pulse tracking-wider">LOADING...</div>
        )}

        {/* Episodes */}
        {!loading && tab === "episodes" && (
          <div className="space-y-1 max-w-2xl">
            {episodes.length === 0 && (
              <div className="font-mono text-[10px] text-muted-foreground/40 tracking-wider">
                NO EPISODES YET. GENERATED AFTER 3+ TURNS.
              </div>
            )}
            {episodes.map((ep) => (
              <div
                key={ep.id}
                className="bg-card border-l-2 border-border px-4 py-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm text-foreground leading-relaxed">
                      {ep.summary}
                    </p>
                    <div className="flex items-center gap-3 mt-2">
                      <span className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
                        {ep.date}{ep.time ? ` ${ep.time}` : ""}
                      </span>
                      {ep.emotionalArc && (
                        <>
                          <span className="text-border">|</span>
                          <span className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
                            {ep.emotionalArc}
                          </span>
                        </>
                      )}
                      {ep.turnCount != null && (
                        <>
                          <span className="text-border">|</span>
                          <span className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
                            {ep.turnCount}T
                          </span>
                        </>
                      )}
                    </div>
                    {ep.topics.length > 0 && (
                      <div className="flex gap-1 mt-2">
                        {ep.topics.map((topic) => (
                          <span
                            key={topic}
                            className="font-mono text-[8px] px-1.5 py-0.5 bg-input border border-border tracking-wider text-muted-foreground/50"
                          >
                            {topic.toUpperCase()}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="shrink-0 flex gap-px">
                    {[1, 2, 3, 4, 5].map((n) => (
                      <div
                        key={n}
                        className={`w-1 h-3 ${
                          n <= ep.significanceScore
                            ? "bg-primary/60"
                            : "bg-border/50"
                        }`}
                      />
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Memory items */}
        {!loading && tab !== "episodes" && (
          <div className="space-y-1 max-w-2xl">
            {items.length === 0 && !showCreate && (
              <div className="font-mono text-[10px] text-muted-foreground/40 tracking-wider">
                NO {tab.toUpperCase()} YET. EXTRACTED FROM CONVERSATIONS OR ADD MANUALLY.
              </div>
            )}

            {items.map((item) => (
              <div
                key={item.id}
                className="group bg-card border-l-2 border-border px-4 py-2.5 flex items-start gap-3 hover:border-primary/20 transition-colors"
              >
                {editingId === item.id ? (
                  <div className="flex-1 flex flex-col gap-2">
                    <input
                      type="text"
                      value={editContent}
                      onChange={(e) => setEditContent(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") saveEdit(item);
                        if (e.key === "Escape") cancelEdit();
                      }}
                      className="w-full bg-input border border-border px-2 py-1 text-sm text-foreground outline-none focus:border-primary/40"
                      autoFocus
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={() => saveEdit(item)}
                        className="font-mono text-[9px] text-muted-foreground hover:text-foreground tracking-wider"
                      >
                        SAVE
                      </button>
                      <button
                        onClick={cancelEdit}
                        className="font-mono text-[9px] text-muted-foreground hover:text-foreground tracking-wider"
                      >
                        CANCEL
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-foreground leading-relaxed">
                        {item.content}
                      </p>
                      <div className="flex items-center gap-2 mt-1">
                        <span className="font-mono text-[8px] text-muted-foreground/30 tracking-wider">
                          {IMPORTANCE_LABELS[item.importance] || ""}
                        </span>
                        <span className="text-border">|</span>
                        <span className="font-mono text-[8px] text-muted-foreground/30 tracking-wider">
                          {item.source?.toUpperCase()}
                        </span>
                        {item.createdAt && (
                          <>
                            <span className="text-border">|</span>
                            <span className="font-mono text-[8px] text-muted-foreground/30">
                              {new Date(item.createdAt).toLocaleDateString()}
                            </span>
                          </>
                        )}
                      </div>
                    </div>
                    <div className="shrink-0 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button
                        onClick={() => startEdit(item)}
                        className="font-mono text-[9px] text-muted-foreground/40 hover:text-foreground tracking-wider"
                      >
                        EDIT
                      </button>
                      <button
                        onClick={() => deleteItem(item.id)}
                        className="font-mono text-[9px] text-muted-foreground/40 hover:text-destructive tracking-wider"
                      >
                        DEL
                      </button>
                    </div>
                  </>
                )}
              </div>
            ))}

            {/* Create */}
            {showCreate ? (
              <div className="bg-card border-l-2 border-primary/30 px-4 py-3 space-y-2.5">
                <input
                  type="text"
                  value={newContent}
                  onChange={(e) => setNewContent(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && newContent.trim()) handleCreate();
                    if (e.key === "Escape") { setShowCreate(false); setNewContent(""); }
                  }}
                  placeholder={`e.g. "${tab === "facts" ? "Works as a designer" : tab === "preferences" ? "Prefers dark mode" : tab === "goals" ? "Learn Rust this year" : "Has a cat named Luna"}"`}
                  className="w-full bg-input border border-border px-2 py-1.5 text-sm text-foreground placeholder:text-muted-foreground/25 outline-none focus:border-primary/40"
                  autoFocus
                />
                <div className="flex items-center gap-3">
                  <label className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
                    IMP
                  </label>
                  <div className="flex gap-px">
                    {[1, 2, 3, 4, 5].map((n) => (
                      <button
                        key={n}
                        onClick={() => setNewImportance(n)}
                        className={`w-5 h-5 font-mono text-[9px] border transition-colors ${
                          n === newImportance
                            ? "bg-primary/[0.08] text-primary border-primary/40"
                            : "bg-input text-muted-foreground/40 border-border hover:border-text-muted/30"
                        }`}
                      >
                        {n}
                      </button>
                    ))}
                  </div>
                  <span className="font-mono text-[8px] text-muted-foreground/30 tracking-wider">
                    {IMPORTANCE_LABELS[newImportance]?.toUpperCase()}
                  </span>
                  <div className="ml-auto flex gap-2">
                    <button
                      onClick={() => { setShowCreate(false); setNewContent(""); }}
                      className="font-mono text-[9px] text-muted-foreground/40 hover:text-foreground tracking-wider"
                    >
                      CANCEL
                    </button>
                    <button
                      onClick={handleCreate}
                      disabled={!newContent.trim()}
                      className="font-mono text-[9px] text-muted-foreground/40 hover:text-primary tracking-wider disabled:opacity-20"
                    >
                      ADD
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <button
                onClick={() => setShowCreate(true)}
                className="w-full text-left font-mono text-[9px] text-muted-foreground/30 hover:text-muted-foreground/50 tracking-wider py-2 px-4 border border-dashed border-border hover:border-text-muted/20 transition-colors"
              >
                + ADD {categoryForTab(tab).toUpperCase()}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
