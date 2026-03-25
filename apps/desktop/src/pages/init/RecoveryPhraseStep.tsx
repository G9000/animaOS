import { useState } from "react";
import { Button } from "@anima/standard-templates";
import { COPY } from "./constants";

interface RecoveryPhraseStepProps {
  phrase: string;
  onContinue: () => void;
  bottomRef: React.RefObject<HTMLDivElement | null>;
}

const WORD_DELAY = 80;

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
    <div className="text-center" ref={bottomRef}>
      {/* Header */}
      <p className="text-caption font-mono tracking-widest uppercase text-muted-foreground mb-1 animate-fade-in">
        {COPY.recoveryLabel}
      </p>
      <p
        className="text-detail font-mono text-subtle-foreground mb-8 animate-fade-in leading-relaxed"
        style={{ animationDelay: "150ms", animationFillMode: "backwards" }}
      >
        {COPY.recoverySub}
      </p>

      {/* Word grid */}
      <div className="grid grid-cols-3 max-w-xs mx-auto mb-8 font-mono border border-border">
        {words.map((word, i) => (
          <div
            key={i}
            className="flex items-baseline gap-2 px-3 py-1.5 animate-fade-in border-border [&:not(:nth-child(3n))]:border-r [&:not(:nth-last-child(-n+3))]:border-b"
            style={{ animationDelay: `${200 + i * WORD_DELAY}ms`, animationFillMode: "backwards" }}
          >
            <span className="text-label text-subtle-foreground shrink-0 w-4 text-right tabular-nums">
              {i + 1}
            </span>
            <span className="text-body text-muted-foreground">
              {word}
            </span>
          </div>
        ))}
      </div>

      {/* Actions */}
      <div
        className="flex items-center justify-center gap-3 animate-fade-in"
        style={{ animationDelay: `${allRevealedAt}ms`, animationFillMode: "backwards" }}
      >
        <Button
          size="sm"
          variant="ghost"
          onClick={(e) => { e.stopPropagation(); copy(); }}
        >
          {copied ? "copied ✓" : "copy phrase"}
        </Button>
        <Button
          size="sm"
          onClick={(e) => { e.stopPropagation(); onContinue(); }}
        >
          continue
        </Button>
      </div>
    </div>
  );
}
