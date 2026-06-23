import { useState } from "react";
import { getTranslateLang, setTranslateLang, LANGUAGES } from "../../lib/preferences";

const glass = "bg-background/25 backdrop-blur-[40px] border border-foreground/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.22)]";

export default function LanguageSettings() {
  const [currentLang, setCurrentLang] = useState(getTranslateLang());

  const handleLangChange = (code: string) => {
    setCurrentLang(code as typeof currentLang);
    setTranslateLang(code as typeof currentLang);
  };

  return (
    <div className={`${glass} p-6 space-y-5`}>
      <h2 className="font-mono text-[9px] tracking-[0.22em] uppercase text-foreground/40">
        Translation Language
      </h2>

      <p className="font-mono text-[10px] text-foreground/30 tracking-wide leading-relaxed">
        Select your preferred language for message translation in chat.
      </p>

      <div className="h-px bg-foreground/[0.06]" />

      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-1.5">
        {LANGUAGES.map((lang) => {
          const isSelected = currentLang === lang.code;
          return (
            <button
              key={lang.code}
              onClick={() => handleLangChange(lang.code)}
              className={[
                "flex items-center justify-between px-3 py-2.5 text-left font-mono text-[10px] tracking-[0.14em] border transition-all",
                isSelected
                  ? "border-foreground/[0.18] bg-foreground/[0.08] text-foreground"
                  : "border-foreground/[0.07] text-foreground/35 hover:text-foreground/65 hover:border-foreground/[0.14] hover:bg-foreground/[0.04]",
              ].join(" ")}
            >
              <span>{lang.label.toUpperCase()}</span>
              {isSelected && <span className="text-accent/70 text-[8px]">◆</span>}
            </button>
          );
        })}
      </div>

      <div className="h-px bg-foreground/[0.06]" />

      <p className="font-mono text-[9px] text-foreground/20 tracking-[0.24em] uppercase select-none">
        Active · {LANGUAGES.find((l) => l.code === currentLang)?.label.toUpperCase()} [{currentLang}]
      </p>
    </div>
  );
}
