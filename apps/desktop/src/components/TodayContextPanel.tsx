import { useEffect, useState } from "react";
import type { TodayContext } from "@anima/api-client";
import type { TodayContextDraft } from "../lib/today-context";

const MOOD_OPTIONS = [
  { value: "steady", emoji: "🙂", label: "steady" },
  { value: "tired", emoji: "😵", label: "tired" },
  { value: "anxious", emoji: "😟", label: "anxious" },
  { value: "overwhelmed", emoji: "🌊", label: "overwhelmed" },
  { value: "frustrated", emoji: "😤", label: "frustrated" },
  { value: "low", emoji: "😔", label: "low" },
  { value: "energized", emoji: "🔥", label: "energized" },
  { value: "good", emoji: "✨", label: "good" },
] as const;

const ENERGY_OPTIONS = ["low", "steady", "high"] as const;
const PRIMARY_MOOD_VALUES = new Set(["steady", "tired", "anxious", "energized"]);

export function TodayContextPanel({
  context,
  greeting,
  suggestion = null,
  defaultExpanded = false,
  onSave,
  onClear,
  onAcceptSuggestion,
  onDismissSuggestion,
}: {
  context: TodayContext | null;
  greeting?: string | null;
  suggestion?: TodayContext | null;
  defaultExpanded?: boolean;
  onSave: (draft: TodayContextDraft) => void;
  onClear: () => void;
  onAcceptSuggestion?: () => void;
  onDismissSuggestion?: () => void;
}) {
  const [mood, setMood] = useState(context?.mood ?? "");
  const [energy, setEnergy] = useState(context?.energy ?? "");
  const [note, setNote] = useState(context?.note ?? "");
  const [expanded, setExpanded] = useState(defaultExpanded);

  useEffect(() => {
    setMood(context?.mood ?? "");
    setEnergy(context?.energy ?? "");
    setNote(context?.note ?? "");
  }, [context]);

  const hasDraft = Boolean(mood.trim() || energy.trim() || note.trim());
  const hasContext = context !== null;
  const commitDraft = (draft: TodayContextDraft) => onSave(draft);
  const visibleMoodOptions = expanded
    ? MOOD_OPTIONS
    : MOOD_OPTIONS.filter((option) => PRIMARY_MOOD_VALUES.has(option.value));
  const summaryParts = [
    mood.trim() || null,
    energy.trim() ? `${energy.trim()} energy` : null,
  ].filter((part): part is string => Boolean(part));
  const summaryText = hasContext && summaryParts.length > 0
    ? `Today: ${summaryParts.join(" · ")}`
    : null;
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
        <div className="min-w-0">
          <span className="font-mono text-[9px] tracking-[0.2em] uppercase text-muted-foreground/55">
            Today
          </span>
          {summaryText && (
            <p className="mt-1 truncate text-xs text-muted-foreground">
              {summaryText}
            </p>
          )}
        </div>
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
          {expanded && (
            <button
              type="button"
              onClick={() => commitDraft({ mood, energy, note })}
              disabled={!hasDraft}
              className="border border-border px-2.5 py-1 font-mono text-[9px] tracking-[0.18em] text-muted-foreground hover:text-foreground disabled:opacity-30"
            >
              UPDATE
            </button>
          )}
          <button
            type="button"
            aria-label={
              expanded
                ? "Show fewer today context controls"
                : "Show more today context controls"
            }
            onClick={() => setExpanded((value) => !value)}
            className="border border-border px-2 py-1 font-mono text-[10px] leading-none text-muted-foreground hover:text-foreground"
          >
            {expanded ? "-" : "+"}
          </button>
        </div>
      </div>
      {!hasContext && greeting && !summaryText && (
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
      <div className="mb-2 grid grid-cols-4 gap-1.5">
        {visibleMoodOptions.map((option) => {
          const selected = mood.trim().toLowerCase() === option.value;
          return (
            <button
              key={option.value}
              type="button"
              aria-label={`Set mood ${option.value}`}
              onClick={() => {
                setMood(option.value);
                commitDraft({ mood: option.value, energy, note });
              }}
              className={[
                "min-w-0 border px-1.5 py-1.5 text-[11px] leading-tight transition-colors",
                selected
                  ? "border-foreground/40 bg-foreground/10 text-foreground"
                  : "border-border bg-background text-muted-foreground hover:text-foreground",
              ].join(" ")}
            >
              <span aria-hidden="true" className="mr-1">
                {option.emoji}
              </span>
              <span className="align-middle">{option.label}</span>
            </button>
          );
        })}
      </div>
      <div className="mb-2 grid grid-cols-3 gap-1.5">
        {ENERGY_OPTIONS.map((value) => {
          const selected = energy.trim().toLowerCase() === value;
          return (
            <button
              key={value}
              type="button"
              aria-label={`Set energy ${value}`}
              onClick={() => {
                setEnergy(value);
                commitDraft({ mood, energy: value, note });
              }}
              className={[
                "border px-2 py-1.5 font-mono text-[9px] uppercase tracking-[0.16em] transition-colors",
                selected
                  ? "border-foreground/40 bg-foreground/10 text-foreground"
                  : "border-border bg-background text-muted-foreground hover:text-foreground",
              ].join(" ")}
            >
              {value}
            </button>
          );
        })}
      </div>
      {expanded && (
        <>
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
        </>
      )}
    </div>
  );
}
