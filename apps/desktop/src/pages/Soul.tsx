import { useState, useEffect, useRef } from "react";
import { api } from "../lib/api";
import { useAuth } from "../context/AuthContext";

export default function Soul() {
  const { user } = useAuth();
  const [content, setContent] = useState("");
  const [original, setOriginal] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const hasChanges = content !== original;

  useEffect(() => {
    if (!user) return;
    api.soul
      .get(user.id)
      .then((data) => {
        setContent(data.content || "");
        setOriginal(data.content || "");
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load soul"),
      )
      .finally(() => setLoading(false));
  }, [user]);

  const handleSave = async () => {
    setSaving(true);
    setError("");
    setSaved(false);

    try {
      if (!user) throw new Error("User not found");
      await api.soul.update(user.id, content);

      setOriginal(content);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setContent(original);
    setError("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "s") {
      e.preventDefault();
      if (hasChanges && !saving) handleSave();
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-5 py-2.5 border-b border-border bg-card/40">
        <div className="flex items-center gap-3">
          <span className="font-mono text-[10px] text-muted-foreground tracking-wider">
            DIRECTIVE
          </span>
          {hasChanges && (
            <>
              <div className="w-px h-3 bg-border" />
              <span className="font-mono text-[9px] text-warning/60 tracking-wider">
                UNSAVED
              </span>
            </>
          )}
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleReset}
            disabled={!hasChanges || saving}
            className="font-mono text-[9px] text-muted-foreground/40 hover:text-foreground disabled:opacity-15 tracking-wider transition-colors"
          >
            RESET
          </button>
          <button
            onClick={handleSave}
            disabled={!hasChanges || saving}
            className="font-mono px-3 py-1 text-[9px] tracking-wider bg-primary/[0.08] text-primary border border-primary/30 hover:bg-primary/[0.12] disabled:opacity-20 transition-colors"
          >
            {saving ? "SAVING..." : "SAVE"}
          </button>
        </div>
      </div>

      {/* Description */}
      <div className="px-5 py-2 border-b border-border/50">
        <p className="font-mono text-[10px] text-muted-foreground/40 leading-relaxed max-w-lg tracking-wider">
          YOUR INSTRUCTIONS TO ANIMA. CHANGES TAKE EFFECT NEXT CONVERSATION.
        </p>
        <p className="font-mono text-[8px] text-muted-foreground/20 mt-0.5 tracking-wider">
          CMD+S SAVE
        </p>
      </div>

      {/* Editor */}
      <div className="flex-1 p-4 min-h-0">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <span className="font-mono text-[10px] text-muted-foreground/30 animate-pulse tracking-wider">
              LOADING...
            </span>
          </div>
        ) : (
          <textarea
            ref={textareaRef}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={handleKeyDown}
            spellCheck={false}
            className="w-full h-full bg-input border border-border px-5 py-4 text-sm text-foreground outline-none focus:border-primary/30 transition-colors resize-none leading-relaxed font-mono"
          />
        )}
      </div>

      {/* Status bar */}
      <div className="px-5 py-2 border-t border-border flex items-center gap-4">
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
        <span className="ml-auto font-mono text-[8px] text-muted-foreground/20 tracking-wider">
          {content.length} CHARS | {content.split("\n").length} LINES
        </span>
      </div>
    </div>
  );
}
