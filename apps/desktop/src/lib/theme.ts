const THEME_KEY = "anima-theme";

export type Theme = "dark" | "light" | "system";
export const THEME_CHANGED_EVENT = "anima-theme-changed";

function getSystemTheme(): "dark" | "light" {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function getTheme(): Theme {
  return (localStorage.getItem(THEME_KEY) as Theme) ?? "dark";
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
  localStorage.setItem(THEME_KEY, theme);
  applyTheme(getEffectiveTheme());
  dispatchThemeChanged();
  return theme;
}

export function toggleTheme(): Theme {
  const current = getEffectiveTheme();
  return setTheme(current === "dark" ? "light" : "dark");
}
