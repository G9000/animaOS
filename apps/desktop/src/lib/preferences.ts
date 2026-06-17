import { dispatchBannerChanged, dispatchSettingsChanged } from "./events";

const BANNER_STORAGE_KEY = "anima-dashboard-banner";
const BANNER_MAX_BYTES = 5 * 1024 * 1024;

export { BANNER_MAX_BYTES };

export interface BannerConfig {
  url: string;
  x: number; // object-position x, 0–100
  y: number; // object-position y, 0–100
}

export function getCustomBanner(): BannerConfig | null {
  try {
    const raw = localStorage.getItem(BANNER_STORAGE_KEY);
    if (!raw) return null;
    // legacy: plain data URL
    if (raw.startsWith("data:") || raw.startsWith("http")) {
      return { url: raw, x: 50, y: 50 };
    }
    const parsed = JSON.parse(raw);
    if (parsed?.url) return { url: parsed.url, x: parsed.x ?? 50, y: parsed.y ?? 50 };
    return null;
  } catch { return null; }
}

export function saveCustomBanner(config: BannerConfig): void {
  try { localStorage.setItem(BANNER_STORAGE_KEY, JSON.stringify(config)); } catch {}
  dispatchBannerChanged();
}

export function clearCustomBanner(): void {
  try { localStorage.removeItem(BANNER_STORAGE_KEY); } catch {}
  dispatchBannerChanged();
}

const DB_VIEWER_KEY = "anima-debug-db-viewer";
const SHOW_TRACE_KEY = "anima-show-trace";
const TRANSLATE_LANG_KEY = "anima-translate-lang";

export const LANGUAGES = [
  { code: "en", label: "English" },
  { code: "es", label: "Spanish" },
  { code: "fr", label: "French" },
  { code: "de", label: "German" },
  { code: "pt", label: "Portuguese" },
  { code: "ja", label: "Japanese" },
  { code: "ko", label: "Korean" },
  { code: "zh", label: "Chinese" },
  { code: "ar", label: "Arabic" },
  { code: "hi", label: "Hindi" },
  { code: "tl", label: "Filipino" },
  { code: "ru", label: "Russian" },
  { code: "it", label: "Italian" },
  { code: "vi", label: "Vietnamese" },
  { code: "th", label: "Thai" },
] as const;

export type LanguageCode = typeof LANGUAGES[number]["code"];

export function getShowTrace(): boolean {
  try {
    return localStorage.getItem(SHOW_TRACE_KEY) === "true";
  } catch {
    return false;
  }
}

export function setShowTrace(enabled: boolean): void {
  try {
    localStorage.setItem(SHOW_TRACE_KEY, String(enabled));
  } catch {}
  dispatchSettingsChanged();
}

export function getDbViewerEnabled(): boolean {
  try {
    return localStorage.getItem(DB_VIEWER_KEY) === "true";
  } catch {
    return false;
  }
}

export function setDbViewerEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(DB_VIEWER_KEY, String(enabled));
  } catch {
    // Ignore storage failures and still notify listeners.
  }

  dispatchSettingsChanged();
}

export function getTranslateLang(): LanguageCode {
  try {
    const stored = localStorage.getItem(TRANSLATE_LANG_KEY);
    if (stored) return stored as LanguageCode;
  } catch {}
  return "en";
}

export function setTranslateLang(code: LanguageCode): void {
  try {
    localStorage.setItem(TRANSLATE_LANG_KEY, code);
  } catch {
    // Ignore storage failures
  }
}
