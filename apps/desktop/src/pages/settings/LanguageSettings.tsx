import { useState } from "react";
import { getTranslateLang, setTranslateLang, LANGUAGES } from "../../lib/preferences";

export default function LanguageSettings() {
  const [currentLang, setCurrentLang] = useState(getTranslateLang());

  const handleLangChange = (code: string) => {
    setCurrentLang(code as typeof currentLang);
    setTranslateLang(code as typeof currentLang);
  };

  return (
    <div className="space-y-6">
      <section className="border border-border bg-card p-5 space-y-5">
        <header className="space-y-1">
          <h2 className="text-[11px] text-muted-foreground uppercase tracking-wider">
            Translation
          </h2>
          <p className="text-xs text-muted-foreground">
            Select your preferred language for message translation in chat.
          </p>
        </header>

        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2">
          {LANGUAGES.map((lang) => {
            const isSelected = currentLang === lang.code;
            return (
              <button
                key={lang.code}
                onClick={() => handleLangChange(lang.code)}
                className={`flex items-center justify-between px-3 py-2 text-left font-mono text-[11px] tracking-wider border transition-colors ${
                  isSelected
                    ? "bg-primary/[0.06] border-primary text-foreground"
                    : "bg-card border-border/60 text-muted-foreground hover:text-foreground hover:border-border"
                }`}
              >
                <span>{lang.label.toUpperCase()}</span>
                {isSelected && (
                  <svg className="w-3.5 h-3.5 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                )}
              </button>
            );
          })}
        </div>

        <div className="pt-2 text-[10px] text-muted-foreground/50 font-mono tracking-wider">
          CURRENT: {LANGUAGES.find((l) => l.code === currentLang)?.label.toUpperCase()} ({currentLang})
        </div>
      </section>
    </div>
  );
}
