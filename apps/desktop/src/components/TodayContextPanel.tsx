import { useEffect, useState } from "react";
import type { TodayContext } from "@anima/api-client";
import type { TodayContextDraft } from "../lib/today-context";

export function TodayContextPanel({
  context,
  greeting,
  suggestion = null,
  onSave,
  onClear,
  onAcceptSuggestion,
  onDismissSuggestion,
}: {
  context: TodayContext | null;
  greeting?: string | null;
  suggestion?: TodayContext | null;
  onSave: (draft: TodayContextDraft) => void;
  onClear: () => void;
  onAcceptSuggestion?: () => void;
  onDismissSuggestion?: () => void;
}) {
  const [mood, setMood] = useState(context?.mood ?? "");
  const [energy, setEnergy] = useState(context?.energy ?? "");
  const [note, setNote] = useState(context?.note ?? "");

  useEffect(() => {
    setMood(context?.mood ?? "");
    setEnergy(context?.energy ?? "");
    setNote(context?.note ?? "");
  }, [context]);

  const hasDraft = Boolean(mood.trim() || energy.trim() || note.trim());
  const hasContext = context !== null;
  const suggestionItems = suggestion
    ? [
        suggestion.mood ? `Mood: ${suggestion.mood}` : null,
        suggestion.energy ? `Energy: ${suggestion.energy}` : null,
        suggestion.note ? `Note: ${suggestion.note}` : null,
      ].filter((item): item is string => Boolean(item))
    : [];

  return (
    <div className="mb-2 border border-border bg-card px-3 py-2.5">
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="font-mono text-[9px] tracking-[0.2em] uppercase text-muted-foreground/55">
          Today
        </span>
        <div className="flex items-center gap-2">
          {hasContext && (
            <button
              type="button"
              onClick={onClear}
              className="font-mono text-[9px] tracking-[0.18em] text-muted-foreground/35 hover:text-muted-foreground"
            >
              CLEAR
            </button>
          )}
          <button
            type="button"
            onClick={() => onSave({ mood, energy, note })}
            disabled={!hasDraft}
            className="border border-border px-2.5 py-1 font-mono text-[9px] tracking-[0.18em] text-muted-foreground hover:text-foreground disabled:opacity-30"
          >
            UPDATE
          </button>
        </div>
      </div>
      {!hasContext && greeting && (
        <p className="mb-2 text-xs leading-relaxed text-muted-foreground">
          {greeting}
        </p>
      )}
      {!hasContext && suggestion && suggestionItems.length > 0 && (
        <div className="mb-2 border-t border-border/60 pt-2">
          <div className="mb-1.5 flex items-center justify-between gap-3">
            <span className="font-mono text-[9px] tracking-[0.18em] uppercase text-muted-foreground/45">
              Suggested
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onDismissSuggestion}
                className="font-mono text-[9px] tracking-[0.18em] text-muted-foreground/35 hover:text-muted-foreground"
              >
                DISMISS
              </button>
              <button
                type="button"
                onClick={onAcceptSuggestion}
                className="border border-border px-2 py-1 font-mono text-[9px] tracking-[0.18em] text-muted-foreground hover:text-foreground"
              >
                ACCEPT
              </button>
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {suggestionItems.map((item) => (
              <span
                key={item}
                className="max-w-full break-words border border-border/70 px-1.5 py-0.5 text-[11px] leading-snug text-muted-foreground"
              >
                {item}
              </span>
            ))}
          </div>
        </div>
      )}
      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,0.8fr)]">
        <input
          value={mood}
          onChange={(event) => setMood(event.currentTarget.value)}
          placeholder="mood"
          maxLength={80}
          className="min-w-0 bg-background border border-border px-2 py-1.5 text-xs text-foreground placeholder:text-muted-foreground/30 outline-none focus:border-muted-foreground/40"
        />
        <input
          value={energy}
          onChange={(event) => setEnergy(event.currentTarget.value)}
          placeholder="energy"
          maxLength={40}
          className="min-w-0 bg-background border border-border px-2 py-1.5 text-xs text-foreground placeholder:text-muted-foreground/30 outline-none focus:border-muted-foreground/40"
        />
      </div>
      <input
        value={note}
        onChange={(event) => setNote(event.currentTarget.value)}
        placeholder="note"
        maxLength={280}
        className="mt-2 w-full bg-background border border-border px-2 py-1.5 text-xs text-foreground placeholder:text-muted-foreground/30 outline-none focus:border-muted-foreground/40"
      />
    </div>
  );
}
