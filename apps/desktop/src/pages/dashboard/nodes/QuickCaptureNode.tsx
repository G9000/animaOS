import { useState } from "react";
import type { NodeProps } from "@xyflow/react";
import type { QuickCaptureNode } from "./node-types";
import { cn, glassPanel } from "@anima/standard-templates";

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
        className="absolute -top-5 right-0 z-20 h-4 px-1.5 flex items-center rounded-sm bg-background/60 border border-hairline-faint font-mono text-micro text-foreground/25 hover:text-foreground/60 hover:bg-background/80 opacity-0 group-hover:opacity-100 transition-all duration-200 backdrop-blur-sm"
        aria-label="Close capture"
      >
        ×
      </button>

      <div className={cn(glassPanel, "overflow-hidden flex flex-col")}>

        {/* Header */}
        <div className="shrink-0 flex items-center justify-between px-3.5 h-9 border-b border-hairline-faint">
          <span className="font-mono text-micro tracking-caps-5 uppercase text-foreground/30">
            Capture
          </span>
          <span className="font-mono text-nano tracking-wider text-foreground/15">
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
          className="bg-transparent px-3.5 py-3 font-mono text-caption tracking-wide text-foreground/65 placeholder:text-foreground/18 focus:outline-none resize-none leading-relaxed nowheel"
        />

        {/* Footer */}
        <div className="shrink-0 px-3.5 h-8 border-t border-hairline-faint flex items-center justify-end gap-3">
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
                className="font-mono text-[7.5px] tracking-caps-4 uppercase text-foreground/30 hover:text-foreground/70 disabled:opacity-20 disabled:cursor-default transition-colors"
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
