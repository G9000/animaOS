import { useState, useEffect } from "react";

export default function ThemeToggle() {
  const [dark, setDark] = useState(true);

  useEffect(() => {
    // Read current state from html class (set by inline script in BaseLayout)
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
  }

  return (
    <button
      onClick={toggle}
      className="font-mono text-[9px] tracking-[0.2em] uppercase text-muted-foreground/30 hover:text-foreground transition-colors"
      aria-label="toggle theme"
    >
      {dark ? "light" : "dark"}
    </button>
  );
}
