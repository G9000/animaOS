import { useEffect, useState } from "react";
import {
  getEffectiveTheme,
  getTheme,
  setTheme,
  toggleTheme,
  THEME_CHANGED_EVENT,
  type Theme,
} from "../lib/theme";

export interface UseThemeResult {
  theme: Theme;
  effective: "dark" | "light";
  set: (theme: Theme) => void;
  toggle: () => void;
}

export function useTheme(): UseThemeResult {
  const [theme, setThemeState] = useState<Theme>(getTheme);
  const [effective, setEffective] = useState<"dark" | "light">(getEffectiveTheme);

  useEffect(() => {
    const handler = () => {
      setThemeState(getTheme());
      setEffective(getEffectiveTheme());
    };

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const mediaHandler = () => {
      if (getTheme() === "system") {
        setEffective(getEffectiveTheme());
      }
    };

    window.addEventListener(THEME_CHANGED_EVENT, handler);
    media.addEventListener("change", mediaHandler);

    return () => {
      window.removeEventListener(THEME_CHANGED_EVENT, handler);
      media.removeEventListener("change", mediaHandler);
    };
  }, []);

  return {
    theme,
    effective,
    set: (next: Theme) => setThemeState(setTheme(next)),
    toggle: () => setThemeState(toggleTheme()),
  };
}
