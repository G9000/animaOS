import { useState } from "react";
import { getTranslateLang, setTranslateLang, LANGUAGES } from "../../lib/preferences";

const glass =
  "bg-background/25 backdrop-blur-[40px] border border-foreground/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.22)]";

/** Native-script representation shown alongside the Latin label */
const NATIVE: Record<string, string> = {
  en: "English",
  es: "Español",
  fr: "Français",
  de: "Deutsch",
  pt: "Português",
  ja: "日本語",
  ko: "한국어",
  zh: "中文",
  ar: "العربية",
  hi: "हिन्दी",
  tl: "Filipino",
  ru: "Русский",
  it: "Italiano",
  vi: "Tiếng Việt",
  th: "ภาษาไทย",
};

export default function LanguageSettings() {
  const [currentLang, setCurrentLang] = useState(getTranslateLang());
  const [query, setQuery] = useState("");

  const handleLangChange = (code: string) => {
    setCurrentLang(code as typeof currentLang);
    setTranslateLang(code as typeof currentLang);
  };

  const filtered = query.trim()
    ? LANGUAGES.filter(
        (l) =>
          l.label.toLowerCase().includes(query.toLowerCase()) ||
          (NATIVE[l.code] ?? "").toLowerCase().includes(query.toLowerCase()) ||
          l.code.toLowerCase().includes(query.toLowerCase()),
      )
    : LANGUAGES;

  const selected = LANGUAGES.find((l) => l.code === currentLang);

  return (
    <div className="space-y-5 max-w-xl">

      {/* Header */}
      <div className={`${glass} px-6 py-5`}>
        <p className="font-mono text-[9px] tracking-[0.32em] uppercase text-foreground/30 mb-3">
          Translation Language
        </p>
        <div className="flex items-end justify-between gap-4">
          <div>
            <p className="font-mono text-[22px] tracking-tight text-foreground/90 leading-none">
              {NATIVE[currentLang] ?? selected?.label}
            </p>
            <p className="font-mono text-[10px] tracking-[0.18em] text-foreground/30 mt-1.5 uppercase">
              {selected?.label} · {currentLang.toUpperCase()}
            </p>
          </div>
          <div className="w-1 h-8 bg-accent/60 shrink-0" />
        </div>
      </div>

      {/* Search + grid */}
      <div className={`${glass} px-6 py-5 space-y-4`}>
        {/* Filter input */}
        <div className="flex items-center gap-3 border-b border-foreground/[0.08] pb-4">
          <span className="font-mono text-[10px] text-foreground/20 shrink-0 tracking-widest">
            ⌕
          </span>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="search languages…"
            className="flex-1 bg-transparent font-mono text-[10px] tracking-[0.14em] text-foreground/70 placeholder:text-foreground/20 focus:outline-none"
          />
          {query && (
            <button
              onClick={() => setQuery("")}
              className="font-mono text-[10px] text-foreground/20 hover:text-foreground/50 transition-colors"
            >
              ×
            </button>
          )}
        </div>

        {/* Language grid */}
        {filtered.length === 0 ? (
          <p className="font-mono text-[9px] tracking-[0.18em] uppercase text-foreground/20 text-center py-4">
            No match
          </p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-1">
            {filtered.map((lang) => {
              const isSelected = currentLang === lang.code;
              return (
                <button
                  key={lang.code}
                  onClick={() => handleLangChange(lang.code)}
                  className={[
                    "group relative flex flex-col gap-0.5 px-3 py-2.5 text-left border transition-all duration-150",
                    isSelected
                      ? "border-accent/40 bg-accent/[0.07]"
                      : "border-foreground/[0.06] hover:border-foreground/[0.12] hover:bg-foreground/[0.04]",
                  ].join(" ")}
                >
                  {/* Active bar */}
                  {isSelected && (
                    <span className="absolute left-0 top-0 bottom-0 w-[2px] bg-accent/70" />
                  )}

                  <span
                    className={[
                      "font-mono text-[11px] tracking-[0.06em] leading-tight transition-colors",
                      isSelected
                        ? "text-foreground/90"
                        : "text-foreground/40 group-hover:text-foreground/65",
                    ].join(" ")}
                  >
                    {NATIVE[lang.code] ?? lang.label}
                  </span>
                  <span
                    className={[
                      "font-mono text-[8px] tracking-[0.22em] uppercase transition-colors",
                      isSelected
                        ? "text-accent/60"
                        : "text-foreground/20 group-hover:text-foreground/35",
                    ].join(" ")}
                  >
                    {lang.code}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Footer note */}
      <p className="font-mono text-[9px] tracking-[0.2em] uppercase text-foreground/15 px-1">
        Used when translating messages in chat
      </p>
    </div>
  );
}
