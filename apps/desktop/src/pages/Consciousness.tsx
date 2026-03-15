import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../context/AuthContext";
import {
  api,
  type SelfModelData,
  type SelfModelSection,
  type EmotionalContextData,
} from "../lib/api";

type Tab = "identity" | "inner_state" | "working_memory" | "growth_log" | "intentions" | "emotions";

const TABS: { key: Tab; label: string }[] = [
  { key: "identity", label: "Identity" },
  { key: "inner_state", label: "Inner State" },
  { key: "working_memory", label: "Working Memory" },
  { key: "intentions", label: "Intentions" },
  { key: "emotions", label: "Emotions" },
  { key: "growth_log", label: "Growth Log" },
];

const SECTION_DESCRIPTIONS: Record<string, string> = {
  identity: "How ANIMA understands itself and its relationship with you. Evolves through conversations.",
  inner_state: "ANIMA's current internal emotional and cognitive state.",
  working_memory: "Short-term context ANIMA is holding onto. Items with [expires: date] are auto-cleaned.",
  growth_log: "Record of how ANIMA has evolved — corrections, insights, and milestones.",
  intentions: "Goals and behavioral rules ANIMA has formed from your interactions.",
  emotions: "Recent emotional signals detected from your conversations.",
};

export default function Consciousness() {
  const { user } = useAuth();
  const [tab, setTab] = useState<Tab>("identity");
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
      setActionMessage(parts.length ? parts.join(", ") : "Maintenance complete — no changes needed");
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
      <div className="px-5 py-3 border-b border-border">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <span className="text-[11px] text-text-muted uppercase tracking-wider">
              Mind
            </span>
            {selfModel && (
              <span className="text-[10px] text-text-muted/50">
                {Object.keys(selfModel.sections).length} sections
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            {actionMessage && (
              <span className="text-[10px] text-text-muted tracking-wider">
                {actionMessage}
              </span>
            )}
            <button
              onClick={handleReflect}
              disabled={reflecting || sleeping}
              className="text-[10px] text-text-muted hover:text-text uppercase tracking-wider transition-colors disabled:opacity-30"
            >
              {reflecting ? "Reflecting..." : "Reflect"}
            </button>
            <button
              onClick={handleSleep}
              disabled={sleeping || reflecting}
              className="text-[10px] text-text-muted hover:text-text uppercase tracking-wider transition-colors disabled:opacity-30"
            >
              {sleeping ? "Running..." : "Sleep tasks"}
            </button>
            {saved && (
              <span className="text-[10px] text-primary tracking-wider uppercase">
                Saved
              </span>
            )}
            {error && (
              <span className="text-[10px] text-danger tracking-wider">
                {error}
              </span>
            )}
            {editing && (
              <>
                <button
                  onClick={cancelEdit}
                  className="text-[10px] text-text-muted hover:text-text uppercase tracking-wider transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="px-3 py-1 bg-bg-card border border-primary text-text text-[10px] uppercase tracking-wider rounded-sm hover:bg-bg-input disabled:opacity-30 transition-colors"
                >
                  {saving ? "Saving..." : "Save"}
                </button>
              </>
            )}
            {!editing && isEditable && currentSection && (
              <button
                onClick={startEdit}
                className="text-[10px] text-text-muted hover:text-text uppercase tracking-wider transition-colors"
              >
                Edit
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="px-5 py-2 border-b border-border flex gap-1">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => {
              setTab(t.key);
              setEditing(false);
            }}
            className={`text-[10px] px-2 py-1 rounded uppercase tracking-wider transition-colors ${
              tab === t.key
                ? "bg-primary text-bg"
                : "text-text-muted hover:text-text bg-bg-card"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Description */}
      <div className="px-5 py-2.5 border-b border-border/50">
        <p className="text-xs text-text-muted leading-relaxed max-w-lg">
          {SECTION_DESCRIPTIONS[tab]}
        </p>
        {editing && (
          <p className="text-[10px] text-text-muted/40 mt-0.5">
            Cmd+S to save · Esc to cancel
          </p>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-5 py-4 min-h-0">
        {loading && (
          <div className="flex items-center justify-center h-full">
            <span className="text-xs text-text-muted animate-pulse uppercase tracking-wider">
              Loading...
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
            className="w-full h-full bg-bg-input border border-border rounded-sm px-5 py-4 text-sm text-text outline-none focus:border-primary transition-colors resize-none leading-relaxed"
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
          <span className="text-[10px] text-text-muted/30">
            v{currentSection.version} · updated by {currentSection.updatedBy}
            {currentSection.updatedAt &&
              ` · ${new Date(currentSection.updatedAt).toLocaleDateString()}`}
          </span>
        </div>
      )}
    </div>
  );
}

function SectionView({ section }: { section: SelfModelSection | null }) {
  if (!section || !section.content.trim()) {
    return (
      <div className="text-xs text-text-muted/60">
        No content yet. This section will be populated as ANIMA learns from your conversations.
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
            className={`text-sm text-text/90 leading-relaxed ${
              isBullet ? "pl-3" : ""
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
      <div className="text-xs text-text-muted/60">
        No emotional data available yet.
      </div>
    );
  }

  return (
    <div className="max-w-2xl space-y-6">
      {/* Dominant emotion */}
      {emotions.dominantEmotion && (
        <div className="bg-bg-card border border-border rounded-md px-4 py-3">
          <span className="text-[10px] text-text-muted/50 uppercase tracking-wider">
            Dominant Emotion
          </span>
          <p className="text-lg text-text mt-1 capitalize">
            {emotions.dominantEmotion}
          </p>
        </div>
      )}

      {/* Synthesized context */}
      {emotions.synthesizedContext && (
        <div>
          <span className="text-[10px] text-text-muted/50 uppercase tracking-wider">
            Emotional Context
          </span>
          <p className="text-sm text-text/80 leading-relaxed mt-1">
            {emotions.synthesizedContext}
          </p>
        </div>
      )}

      {/* Recent signals */}
      {emotions.recentSignals.length > 0 && (
        <div>
          <span className="text-[10px] text-text-muted/50 uppercase tracking-wider">
            Recent Signals
          </span>
          <div className="mt-2 space-y-2">
            {emotions.recentSignals.map((signal, i) => (
              <div
                key={i}
                className="bg-bg-card border border-border rounded-md px-4 py-2.5"
              >
                <div className="flex items-center gap-3">
                  <span className="text-sm text-text capitalize">
                    {signal.emotion}
                  </span>
                  <div className="flex gap-0.5">
                    {[1, 2, 3, 4, 5].map((n) => (
                      <div
                        key={n}
                        className={`w-1.5 h-1.5 rounded-full ${
                          n <= Math.round(signal.confidence * 5)
                            ? "bg-primary"
                            : "bg-border"
                        }`}
                      />
                    ))}
                  </div>
                  <span className="text-[9px] text-text-muted/40 uppercase tracking-wider">
                    {signal.trajectory}
                  </span>
                </div>
                {signal.topic && (
                  <p className="text-xs text-text-muted/60 mt-1">
                    re: {signal.topic}
                  </p>
                )}
                {signal.evidence && (
                  <p className="text-xs text-text-muted/40 mt-0.5 italic">
                    "{signal.evidence}"
                  </p>
                )}
                {signal.createdAt && (
                  <span className="text-[9px] text-text-muted/30 mt-1 block">
                    {new Date(signal.createdAt).toLocaleString()}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {emotions.recentSignals.length === 0 && !emotions.dominantEmotion && (
        <div className="text-xs text-text-muted/60">
          No emotional signals detected yet. They emerge naturally from conversations.
        </div>
      )}
    </div>
  );
}
