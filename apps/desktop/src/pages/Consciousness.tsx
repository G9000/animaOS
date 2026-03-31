import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../context/AuthContext";
import type { SelfModelData, SelfModelSection, EmotionalContextData } from "@anima/api-client";
import { api } from "../lib/api";

type Tab = "persona" | "human" | "identity" | "inner_state" | "working_memory" | "growth_log" | "intentions" | "emotions";

const TABS: { key: Tab; label: string }[] = [
  { key: "persona", label: "PERSONA" },
  { key: "human", label: "HUMAN" },
  { key: "identity", label: "IDENTITY" },
  { key: "inner_state", label: "STATE" },
  { key: "working_memory", label: "W.MEM" },
  { key: "intentions", label: "INTENT" },
  { key: "emotions", label: "EMOTION" },
  { key: "growth_log", label: "GROWTH" },
];

const SECTION_DESCRIPTIONS: Record<string, string> = {
  persona: "ANIMA's personality — how it thinks and expresses itself. Evolves through conversations.",
  human: "What ANIMA knows about you — your identity, preferences, and relationship.",
  identity: "How ANIMA understands itself and its relationship with you.",
  inner_state: "Current internal emotional and cognitive state.",
  working_memory: "Short-term context. Items with [expires: date] are auto-cleaned.",
  growth_log: "Record of evolution — corrections, insights, milestones.",
  intentions: "Goals and behavioral rules formed from interactions.",
  emotions: "Recent emotional signals from conversations.",
};

export default function Consciousness() {
  const { user } = useAuth();
  const [tab, setTab] = useState<Tab>("persona");
  const [selfModel, setSelfModel] = useState<SelfModelData | null>(null);
  const [emotions, setEmotions] = useState<EmotionalContextData | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [reflecting, setReflecting] = useState(false);
  const [sleeping, setSleeping] = useState(false);
  const [actionMessage, setActionMessage] = useState("");

  useEffect(() => {
    if (user?.id == null) return;
    setLoading(true);
    setError("");

    Promise.all([
      api.consciousness.getSelfModel(user.id),
      api.consciousness.getEmotions(user.id),
    ])
      .then(([sm, em]) => {
        setSelfModel(sm);
        setEmotions(em);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [user?.id]);

  const currentSection: SelfModelSection | null =
    selfModel && tab !== "emotions" ? selfModel.sections[tab] ?? null : null;

  const startEdit = () => {
    if (!currentSection) return;
    setEditContent(currentSection.content);
    setEditing(true);
    setError("");
  };

  const cancelEdit = () => {
    setEditing(false);
    setEditContent("");
  };

  const handleSave = async () => {
    if (user?.id == null || !editing) return;
    setSaving(true);
    setError("");
    try {
      const updated = await api.consciousness.updateSelfModelSection(
        user.id,
        tab,
        editContent,
      );
      setSelfModel((prev) =>
        prev
          ? { ...prev, sections: { ...prev.sections, [tab]: updated } }
          : prev,
      );
      setEditing(false);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "s") {
      e.preventDefault();
      if (editing && !saving) handleSave();
    }
    if (e.key === "Escape") cancelEdit();
  };

  const reload = useCallback(() => {
    if (user?.id == null) return;
    Promise.all([
      api.consciousness.getSelfModel(user.id),
      api.consciousness.getEmotions(user.id),
    ]).then(([sm, em]) => {
      setSelfModel(sm);
      setEmotions(em);
    }).catch(() => {});
  }, [user?.id]);

  const handleReflect = async () => {
    if (user?.id == null || reflecting) return;
    setReflecting(true);
    setActionMessage("");
    try {
      const result = await api.chat.reflect(user.id) as Record<string, unknown>;
      const parts: string[] = [];
      if (result.identityUpdated) parts.push("identity");
      if (result.innerStateUpdated) parts.push("inner state");
      if (result.growthLogEntryAdded) parts.push("growth log");
      if (result.intentionsUpdated) parts.push("intentions");
      setActionMessage(parts.length ? `Updated: ${parts.join(", ")}` : "No changes needed");
      reload();
    } catch (err: any) {
      setActionMessage(`Failed: ${err.message}`);
    } finally {
      setReflecting(false);
      setTimeout(() => setActionMessage(""), 5000);
    }
  };

  const handleSleep = async () => {
    if (user?.id == null || sleeping) return;
    setSleeping(true);
    setActionMessage("");
    try {
      const result = await api.chat.sleep(user.id) as Record<string, unknown>;
      const parts: string[] = [];
      if ((result.contradictionsResolved as number) > 0) parts.push(`${result.contradictionsResolved} contradictions resolved`);
      if ((result.itemsMerged as number) > 0) parts.push(`${result.itemsMerged} items merged`);
      if ((result.episodesGenerated as number) > 0) parts.push(`${result.episodesGenerated} episodes generated`);
      if ((result.embeddingsBackfilled as number) > 0) parts.push(`${result.embeddingsBackfilled} embeddings`);
      setActionMessage(parts.length ? parts.join(", ") : "Maintenance complete");
      reload();
    } catch (err: any) {
      setActionMessage(`Failed: ${err.message}`);
    } finally {
      setSleeping(false);
      setTimeout(() => setActionMessage(""), 5000);
    }
  };

  const isEditable = tab !== "emotions" && tab !== "growth_log";

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-5 py-2.5 border-b border-border bg-card/40">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="font-mono text-[10px] text-muted-foreground tracking-wider">
              MIND
            </span>
            {selfModel && (
              <>
                <div className="w-px h-3 bg-border" />
                <span className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
                  {Object.keys(selfModel.sections).length} SECTIONS
                </span>
              </>
            )}
          </div>
          <div className="flex items-center gap-3">
            {actionMessage && (
              <span className="font-mono text-[9px] text-muted-foreground/60 tracking-wider">
                {actionMessage}
              </span>
            )}
            <button
              onClick={handleReflect}
              disabled={reflecting || sleeping}
              className="font-mono text-[9px] text-muted-foreground/40 hover:text-primary tracking-wider transition-colors disabled:opacity-20"
            >
              {reflecting ? "REFLECTING..." : "REFLECT"}
            </button>
            <button
              onClick={handleSleep}
              disabled={sleeping || reflecting}
              className="font-mono text-[9px] text-muted-foreground/40 hover:text-primary tracking-wider transition-colors disabled:opacity-20"
            >
              {sleeping ? "RUNNING..." : "SLEEP"}
            </button>
            {saved && (
              <span className="font-mono text-[9px] text-primary tracking-wider">
                SAVED
              </span>
            )}
            {error && (
              <span className="font-mono text-[9px] text-destructive tracking-wider">
                {error}
              </span>
            )}
            {editing && (
              <>
                <button
                  onClick={cancelEdit}
                  className="font-mono text-[9px] text-muted-foreground/40 hover:text-foreground tracking-wider transition-colors"
                >
                  CANCEL
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="font-mono px-3 py-1 text-[9px] tracking-wider bg-primary/[0.08] text-primary border border-primary/30 hover:bg-primary/[0.12] disabled:opacity-20 transition-colors"
                >
                  {saving ? "SAVING..." : "SAVE"}
                </button>
              </>
            )}
            {!editing && isEditable && currentSection && (
              <button
                onClick={startEdit}
                className="font-mono text-[9px] text-muted-foreground/40 hover:text-foreground tracking-wider transition-colors"
              >
                EDIT
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="px-5 py-1.5 border-b border-border flex gap-px">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => {
              setTab(t.key);
              setEditing(false);
            }}
            className={`font-mono text-[9px] px-2.5 py-1.5 tracking-wider transition-colors ${
              tab === t.key
                ? "bg-primary/[0.08] text-primary border-b-2 border-primary"
                : "text-muted-foreground/40 hover:text-muted-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Description */}
      <div className="px-5 py-2 border-b border-border/50">
        <p className="font-mono text-[10px] text-muted-foreground/40 leading-relaxed max-w-lg tracking-wider">
          {SECTION_DESCRIPTIONS[tab]}
        </p>
        {editing && (
          <p className="font-mono text-[8px] text-muted-foreground/20 mt-0.5 tracking-wider">
            CMD+S SAVE | ESC CANCEL
          </p>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-5 py-4 min-h-0">
        {loading && (
          <div className="flex items-center justify-center h-full">
            <span className="font-mono text-[10px] text-muted-foreground/30 animate-pulse tracking-wider">
              LOADING...
            </span>
          </div>
        )}

        {!loading && tab === "emotions" && <EmotionsView emotions={emotions} />}

        {!loading && tab !== "emotions" && editing && (
          <textarea
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            onKeyDown={handleKeyDown}
            spellCheck={false}
            className="w-full h-full bg-input border border-border px-5 py-4 text-sm text-foreground outline-none focus:border-primary/30 transition-colors resize-none leading-relaxed font-mono"
            autoFocus
          />
        )}

        {!loading && tab !== "emotions" && !editing && (
          <SectionView section={currentSection} />
        )}
      </div>

      {/* Footer */}
      {currentSection && !editing && (
        <div className="px-5 py-2 border-t border-border flex items-center gap-4">
          <span className="font-mono text-[8px] text-muted-foreground/20 tracking-wider">
            V{currentSection.version} | {currentSection.updatedBy?.toUpperCase()}
            {currentSection.updatedAt &&
              ` | ${new Date(currentSection.updatedAt).toLocaleDateString()}`}
          </span>
        </div>
      )}
    </div>
  );
}

function SectionView({ section }: { section: SelfModelSection | null }) {
  if (!section || !section.content.trim()) {
    return (
      <div className="font-mono text-[10px] text-muted-foreground/40 tracking-wider">
        NO CONTENT YET. POPULATED AS ANIMA LEARNS FROM CONVERSATIONS.
      </div>
    );
  }

  return (
    <div className="max-w-2xl space-y-1">
      {section.content.split("\n").map((line, i) => {
        if (!line.trim()) return <div key={i} className="h-3" />;
        const isBullet = line.trimStart().startsWith("- ");
        return (
          <p
            key={i}
            className={`text-sm text-foreground/85 leading-relaxed ${
              isBullet ? "pl-3 border-l border-border" : ""
            }`}
          >
            {line}
          </p>
        );
      })}
    </div>
  );
}

function EmotionsView({ emotions }: { emotions: EmotionalContextData | null }) {
  if (!emotions) {
    return (
      <div className="font-mono text-[10px] text-muted-foreground/40 tracking-wider">
        NO EMOTIONAL DATA AVAILABLE.
      </div>
    );
  }

  return (
    <div className="max-w-2xl space-y-6">
      {/* Dominant emotion */}
      {emotions.dominantEmotion && (
        <div className="bg-card border-l-2 border-primary/30 px-4 py-3">
          <span className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
            DOMINANT
          </span>
          <p className="text-lg text-foreground mt-1 capitalize">
            {emotions.dominantEmotion}
          </p>
        </div>
      )}

      {/* Synthesized context */}
      {emotions.synthesizedContext && (
        <div>
          <span className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
            CONTEXT
          </span>
          <p className="text-sm text-foreground/70 leading-relaxed mt-1">
            {emotions.synthesizedContext}
          </p>
        </div>
      )}

      {/* Recent signals */}
      {emotions.recentSignals.length > 0 && (
        <div>
          <span className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
            SIGNALS
          </span>
          <div className="mt-2 space-y-px">
            {emotions.recentSignals.map((signal, i) => (
              <div
                key={i}
                className="bg-card border-l-2 border-border px-4 py-2.5 hover:border-primary/20 transition-colors"
              >
                <div className="flex items-center gap-3">
                  <span className="text-sm text-foreground capitalize">
                    {signal.emotion}
                  </span>
                  <div className="flex gap-px">
                    {[1, 2, 3, 4, 5].map((n) => (
                      <div
                        key={n}
                        className={`w-1 h-3 ${
                          n <= Math.round(signal.confidence * 5)
                            ? "bg-primary/60"
                            : "bg-border/50"
                        }`}
                      />
                    ))}
                  </div>
                  <span className="font-mono text-[8px] text-muted-foreground/30 tracking-wider">
                    {signal.trajectory?.toUpperCase()}
                  </span>
                </div>
                {signal.topic && (
                  <p className="font-mono text-[10px] text-muted-foreground/40 mt-1">
                    re: {signal.topic}
                  </p>
                )}
                {signal.evidence && (
                  <p className="text-[11px] text-muted-foreground/30 mt-0.5 italic">
                    "{signal.evidence}"
                  </p>
                )}
                {signal.createdAt && (
                  <span className="font-mono text-[8px] text-muted-foreground/20 mt-1 block tracking-wider">
                    {new Date(signal.createdAt).toLocaleString()}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {emotions.recentSignals.length === 0 && !emotions.dominantEmotion && (
        <div className="font-mono text-[10px] text-muted-foreground/40 tracking-wider">
          NO SIGNALS DETECTED. EMERGE FROM CONVERSATIONS.
        </div>
      )}
    </div>
  );
}
