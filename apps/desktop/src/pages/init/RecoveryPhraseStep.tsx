import { useState } from "react";
import { cn, Button, InfoIcon } from "@anima/standard-templates";
import { COPY } from "./constants";

interface RecoveryPhraseStepProps {
  phrase: string;
  onContinue: () => void;
  bottomRef: React.RefObject<HTMLDivElement | null>;
}

const WORD_DELAY = 80;
const glass = "bg-background/20 backdrop-blur-[44px] border border-foreground/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.24)]";

export function RecoveryPhraseStep({ phrase, onContinue, bottomRef }: RecoveryPhraseStepProps) {
  const [copied, setCopied] = useState(false);
  const words = phrase.split(" ");
  const allRevealedAt = words.length * WORD_DELAY + 300;

  const copy = () => {
    navigator.clipboard.writeText(phrase);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex flex-col items-center gap-3" ref={bottomRef}>

      {/* Single unified container */}
      <div className={cn(glass, "animate-fade-in")}>

        {/* Accent label */}
        <div className="bg-accent px-3 py-1">
          <p className="font-mono text-ui font-semibold tracking-[0.25em] text-foreground uppercase">
            {COPY.recoveryLabel}
          </p>
        </div>

        {/* Hint row */}
        <div className="flex items-start gap-2 px-3 py-2 border-b border-foreground/[0.08]">
          <InfoIcon size="sm" className="text-accent shrink-0 mt-0.5" />
          <p className="font-mono text-detail text-accent tracking-wide uppercase leading-relaxed">
            {COPY.recoverySub}
          </p>
        </div>

        {/* Word grid */}
        <div className="grid grid-cols-3 font-mono">
          {words.map((word, i) => (
            <div
              key={i}
              className="flex items-baseline gap-2 px-3 py-2 animate-fade-in border-foreground/[0.08] [&:not(:nth-child(3n))]:border-r [&:not(:nth-last-child(-n+3))]:border-b"
              style={{ animationDelay: `${250 + i * WORD_DELAY}ms`, animationFillMode: "backwards" }}
            >
              <span className="text-detail text-muted-foreground/40 shrink-0 w-4 text-right tabular-nums">
                {i + 1}
              </span>
              <span className="text-body text-foreground tracking-wide">
                {word}
              </span>
            </div>
          ))}
        </div>

        {/* Copy */}
        <button
          onClick={(e) => { e.stopPropagation(); copy(); }}
          className="w-full px-3 py-2 font-mono text-detail text-muted-foreground/60 uppercase tracking-widest border-t border-foreground/[0.08] hover:text-accent hover:bg-accent/10 transition-colors cursor-pointer text-center"
        >
          {copied ? "✓ copied" : "copy phrase"}
        </button>
      </div>

      {/* Continue */}
      <div
        className={cn(glass, "w-fit animate-fade-in")}
        style={{ animationDelay: `${allRevealedAt}ms`, animationFillMode: "backwards" }}
      >
        <Button
          size="xs"
          variant="main"
          onClick={(e) => { e.stopPropagation(); onContinue(); }}
          className="w-fit h-12"
        >
          i've written it down →
        </Button>
      </div>

    </div>
  );
}
