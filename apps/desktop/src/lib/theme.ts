const THEME_KEY = "anima-theme";

export type Theme = "dark" | "light";

export function getTheme(): Theme {
  return (localStorage.getItem(THEME_KEY) as Theme) ?? "dark";
}

export function initTheme() {
  const theme = getTheme();
  if (theme === "dark") {
    document.documentElement.classList.add("dark");
  } else {
    document.documentElement.classList.remove("dark");
  }
}

export function toggleTheme(): Theme {
  const next = getTheme() === "dark" ? "light" : "dark";
  localStorage.setItem(THEME_KEY, next);
  if (next === "dark") {
    document.documentElement.classList.add("dark");
  } else {
    document.documentElement.classList.remove("dark");
  }
  return next;
}
