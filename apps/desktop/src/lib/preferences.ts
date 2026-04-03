import { dispatchSettingsChanged } from "./events";

const DB_VIEWER_KEY = "anima-debug-db-viewer";

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
