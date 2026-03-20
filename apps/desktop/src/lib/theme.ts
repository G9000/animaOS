const THEME_KEY = "anima-theme";

export type Theme = "dark" | "light";

export function getTheme(): Theme {
  return (localStorage.getItem(THEME_KEY) as Theme) ?? "dark";
}

export function initTheme() {
  document.documentElement.setAttribute("data-theme", getTheme());
}

export function toggleTheme(): Theme {
  const next = getTheme() === "dark" ? "light" : "dark";
  localStorage.setItem(THEME_KEY, next);
  document.documentElement.setAttribute("data-theme", next);
  return next;
}
