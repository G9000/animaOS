import type { TodayContext } from "@anima/api-client";

const STORAGE_KEY = "anima_today_context";
const MOOD_MAX = 80;
const ENERGY_MAX = 40;
const NOTE_MAX = 280;

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
