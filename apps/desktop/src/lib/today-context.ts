import type { TodayContext } from "@anima/api-client";

const STORAGE_KEY = "anima_today_context";
const MOOD_MAX = 80;
const ENERGY_MAX = 40;
const NOTE_MAX = 280;

const MOOD_TERMS = [
  "exhausted",
  "overwhelmed",
  "frustrated",
  "anxious",
  "stressed",
  "drained",
  "tired",
  "sleepy",
  "sad",
  "calm",
  "excited",
  "focused",
  "good",
  "okay",
] as const;

const LOW_ENERGY_TERMS = [
  "exhausted",
  "drained",
  "low energy",
  "tired",
  "sleepy",
  "wiped out",
  "burned out",
  "burnt out",
] as const;

const MEDIUM_ENERGY_TERMS = ["steady", "balanced", "okay"] as const;
const HIGH_ENERGY_TERMS = ["energized", "excited", "wired", "focused"] as const;

const NOTE_PATTERNS: Array<{ pattern: RegExp; note: string }> = [
  {
    pattern: /\bkeep (?:your )?repl(?:y|ies) direct\b/,
    note: "keep replies direct",
  },
  { pattern: /\bkeep it simple\b/, note: "keep it simple" },
  { pattern: /\bshort repl(?:y|ies)\b/, note: "short replies" },
  { pattern: /\bkeep (?:it|this) brief\b/, note: "keep it brief" },
  { pattern: /\bbe gentle\b/, note: "be gentle" },
  { pattern: /\btake it slow\b/, note: "take it slow" },
  { pattern: /\bgo easy\b/, note: "go easy" },
];

export type TodayContextDraft = Omit<TodayContext, "date">;

export interface TodayContextStorage {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
  removeItem: (key: string) => void;
}

export function todayIso(date = new Date()): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function cleanText(value: string | null | undefined, maxLength: number): string {
  return (value ?? "").trim().slice(0, maxLength);
}

function normalizeMessageText(message: string): string {
  return message
    .toLowerCase()
    .replace(/\u2019/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function hasSelfStateSignal(text: string): boolean {
  return /\b(?:i'm|im|i am|i feel|feeling|felt|i've been|ive been|today|right now|currently|this morning|this afternoon|this evening)\b/.test(
    text,
  );
}

function hasTerm(text: string, term: string): boolean {
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`\\b${escaped}\\b`).test(text);
}

function findSelfStateTerm(
  normalizedMessage: string,
  terms: readonly string[],
): string | null {
  const sentences = normalizedMessage
    .split(/[.!?\n]+/)
    .map((sentence) => sentence.trim())
    .filter(Boolean);

  for (const sentence of sentences) {
    if (!hasSelfStateSignal(sentence)) continue;
    const match = terms.find((term) => hasTerm(sentence, term));
    if (match) return match;
  }
  return null;
}

function suggestEnergy(normalizedMessage: string): string | null {
  if (findSelfStateTerm(normalizedMessage, LOW_ENERGY_TERMS)) return "low";
  if (findSelfStateTerm(normalizedMessage, HIGH_ENERGY_TERMS)) return "high";
  if (findSelfStateTerm(normalizedMessage, MEDIUM_ENERGY_TERMS)) return "medium";
  return null;
}

function suggestNote(normalizedMessage: string): string | null {
  return (
    NOTE_PATTERNS.find(({ pattern }) => pattern.test(normalizedMessage))?.note ??
    null
  );
}

function getTodayContextStorage(): TodayContextStorage | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null;
  }
}

export function normalizeTodayContext(
  draft: TodayContextDraft,
  date = todayIso(),
): TodayContext | null {
  const mood = cleanText(draft.mood, MOOD_MAX);
  const energy = cleanText(draft.energy, ENERGY_MAX);
  const note = cleanText(draft.note, NOTE_MAX);
  if (!mood && !energy && !note) return null;
  return {
    date,
    ...(mood ? { mood } : {}),
    ...(energy ? { energy } : {}),
    ...(note ? { note } : {}),
  };
}

export function suggestTodayContextFromMessage(
  message: string,
  date = todayIso(),
): TodayContext | null {
  const normalizedMessage = normalizeMessageText(message);
  if (!normalizedMessage) return null;

  return normalizeTodayContext(
    {
      mood: findSelfStateTerm(normalizedMessage, MOOD_TERMS) ?? undefined,
      energy: suggestEnergy(normalizedMessage) ?? undefined,
      note: suggestNote(normalizedMessage) ?? undefined,
    },
    date,
  );
}

export function loadTodayContext(
  storage: TodayContextStorage | null = getTodayContextStorage(),
  currentDate = todayIso(),
): TodayContext | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<TodayContext> | null;
    if (!parsed || parsed.date !== currentDate) {
      storage.removeItem(STORAGE_KEY);
      return null;
    }
    return normalizeTodayContext(
      {
        mood: parsed.mood,
        energy: parsed.energy,
        note: parsed.note,
      },
      parsed.date,
    );
  } catch {
    storage.removeItem(STORAGE_KEY);
    return null;
  }
}

export function saveTodayContext(
  context: TodayContext | null,
  storage: TodayContextStorage | null = getTodayContextStorage(),
): void {
  if (!storage) return;
  try {
    if (!context) {
      storage.removeItem(STORAGE_KEY);
      return;
    }
    const normalized = normalizeTodayContext(context, context.date);
    if (!normalized) {
      storage.removeItem(STORAGE_KEY);
      return;
    }
    storage.setItem(STORAGE_KEY, JSON.stringify(normalized));
  } catch {
    // Ignore storage failures.
  }
}
