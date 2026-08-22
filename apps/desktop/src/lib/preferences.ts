import { dispatchSettingsChanged } from "./events";
import { getPortablePreference, setPortablePreference } from "./portablePreferences";

const DB_VIEWER_KEY = "anima-debug-db-viewer";
const SHOW_TRACE_KEY = "anima-show-trace";

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
  return getPortablePreference<LanguageCode>("translateLanguage", "en");
}

export function setTranslateLang(code: LanguageCode): void {
  setPortablePreference("translateLanguage", code);
}
