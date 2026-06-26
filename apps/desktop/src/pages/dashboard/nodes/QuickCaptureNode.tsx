import { useState } from "react";
import type { NodeProps } from "@xyflow/react";
import type { QuickCaptureNode } from "./node-types";

type SaveState = "idle" | "saving" | "saved";

export function QuickCaptureNode({ data }: NodeProps<QuickCaptureNode>) {
  const { onSave, onClose } = data;
  const [text, setText] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("idle");

  const handleSave = async () => {
    const trimmed = text.trim();
    if (!trimmed || saveState === "saving") return;
    setSaveState("saving");
    try {
      await onSave(trimmed);
      setText("");
      setSaveState("saved");
      setTimeout(() => setSaveState("idle"), 2000);
    } catch {
      setSaveState("idle");
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      void handleSave();
    }
  };

  return (
    <div className="group relative w-64 overflow-visible">

      <button
        onClick={onClose}
        className="absolute -top-5 right-0 z-20 h-4 px-1.5 flex items-center rounded-sm bg-background/60 border border-foreground/[0.07] font-mono text-[8px] text-foreground/25 hover:text-foreground/60 hover:bg-background/80 opacity-0 group-hover:opacity-100 transition-all duration-200 backdrop-blur-sm"
        aria-label="Close capture"
      >
        ×
      </button>

      <div className="overflow-hidden rounded-xl bg-background/25 backdrop-blur-[36px] border border-foreground/[0.08] shadow-[0_4px_28px_rgba(0,0,0,0.18)] flex flex-col">

        {/* Header */}
        <div className="shrink-0 flex items-center justify-between px-3.5 h-9 border-b border-foreground/[0.06]">
          <span className="font-mono text-[8px] tracking-[0.3em] uppercase text-foreground/30">
            Capture
          </span>
          <span className="font-mono text-[7px] tracking-wider text-foreground/15">
            ⌘↵ to save
          </span>
        </div>

        {/* Textarea */}
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="capture a thought…"
          rows={5}
          className="bg-transparent px-3.5 py-3 font-mono text-[10px] tracking-wide text-foreground/65 placeholder:text-foreground/18 focus:outline-none resize-none leading-relaxed nowheel"
        />

        {/* Footer */}
        <div className="shrink-0 px-3.5 h-8 border-t border-foreground/[0.05] flex items-center justify-end gap-3">
          {saveState === "saved" ? (
            <span className="font-mono text-[7.5px] tracking-wider text-accent/70">
              saved ✓
            </span>
          ) : (
            <>
              {text.trim() && (
                <button
                  onClick={() => setText("")}
                  className="font-mono text-[7.5px] tracking-wider text-foreground/20 hover:text-foreground/50 transition-colors"
                >
                  clear
                </button>
              )}
              <button
                onClick={() => void handleSave()}
                disabled={!text.trim() || saveState === "saving"}
                className="font-mono text-[7.5px] tracking-[0.2em] uppercase text-foreground/30 hover:text-foreground/70 disabled:opacity-20 disabled:cursor-default transition-colors"
              >
                {saveState === "saving" ? "…" : "save →"}
              </button>
            </>
          )}
        </div>

      </div>
    </div>
  );
}
