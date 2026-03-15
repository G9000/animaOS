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
      <div className="flex items-center justify-between px-5 py-2.5 border-b border-border">
        <div className="flex items-center gap-3">
          <span className="text-[11px] text-text-muted uppercase tracking-wider">
            User Directive
          </span>
          {hasChanges && (
            <span className="text-[10px] text-text-muted/60 uppercase tracking-wider">
              · unsaved
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleReset}
            disabled={!hasChanges || saving}
            className="text-[10px] text-text-muted hover:text-text disabled:opacity-20 uppercase tracking-wider transition-colors"
          >
            Reset
          </button>
          <button
            onClick={handleSave}
            disabled={!hasChanges || saving}
            className="px-3 py-1 bg-bg-card border border-primary text-text text-[10px] uppercase tracking-wider rounded-sm hover:bg-bg-input disabled:opacity-30 transition-colors"
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>

      {/* Description */}
      <div className="px-5 py-3 border-b border-border/50">
        <p className="text-xs text-text-muted leading-relaxed max-w-lg">
          Your instructions to ANIMA — how you want it to behave with you.
          Changes take effect on the next conversation.
        </p>
        <p className="text-[10px] text-text-muted/40 mt-0.5">
          Cmd+S to save
        </p>
      </div>

      {/* Editor */}
      <div className="flex-1 p-4 min-h-0">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <span className="text-xs text-text-muted animate-pulse uppercase tracking-wider">
              Loading...
            </span>
          </div>
        ) : (
          <textarea
            ref={textareaRef}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={handleKeyDown}
            spellCheck={false}
            className="w-full h-full bg-bg-input border border-border rounded-sm px-5 py-4 text-sm text-text outline-none focus:border-primary transition-colors resize-none leading-relaxed"
          />
        )}
      </div>

      {/* Status bar */}
      <div className="px-5 py-2 border-t border-border flex items-center gap-4">
        {saved && (
          <span className="text-[10px] text-primary tracking-wider uppercase">
            Saved — takes effect next conversation
          </span>
        )}
        {error && (
          <span className="text-[10px] text-danger tracking-wider">
            {error}
          </span>
        )}
        <span className="ml-auto text-[10px] text-text-muted/30">
          {content.length} chars · {content.split("\n").length} lines
        </span>
      </div>
    </div>
  );
}
