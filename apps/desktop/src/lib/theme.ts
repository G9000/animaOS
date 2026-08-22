import {
  getPortablePreference,
  PORTABLE_PREFERENCES_CHANGED_EVENT,
  setPortablePreference,
} from "./portablePreferences";

export type Theme = "dark" | "light" | "system";
export const THEME_CHANGED_EVENT = "anima-theme-changed";

function getSystemTheme(): "dark" | "light" {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function getTheme(): Theme {
  return getPortablePreference<Theme>("theme", "system");
}

export function getEffectiveTheme(): "dark" | "light" {
  const theme = getTheme();
  return theme === "system" ? getSystemTheme() : theme;
}

function applyTheme(effective: "dark" | "light") {
  if (effective === "dark") {
    document.documentElement.classList.add("dark");
  } else {
    document.documentElement.classList.remove("dark");
  }
}

function dispatchThemeChanged() {
  window.dispatchEvent(new Event(THEME_CHANGED_EVENT));
}

export function initTheme() {
  applyTheme(getEffectiveTheme());
  globalThis.addEventListener(PORTABLE_PREFERENCES_CHANGED_EVENT, () => {
    applyTheme(getEffectiveTheme());
    dispatchThemeChanged();
  });
  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => {
      if (getTheme() === "system") {
        applyTheme(getEffectiveTheme());
        dispatchThemeChanged();
      }
    });
}

export function setTheme(theme: Theme): Theme {
  setPortablePreference("theme", theme);
  applyTheme(getEffectiveTheme());
  dispatchThemeChanged();
  return theme;
}

export function toggleTheme(): Theme {
  const current = getEffectiveTheme();
  return setTheme(current === "dark" ? "light" : "dark");
}
