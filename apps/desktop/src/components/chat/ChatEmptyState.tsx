const scanLines =
  "bg-[repeating-linear-gradient(180deg,transparent_0px,transparent_3px,rgba(255,255,255,0.012)_3px,rgba(255,255,255,0.012)_4px)]";

const STARTERS = [
  "What's on your mind?",
  "Tell me about your day",
  "How are you feeling right now?",
  "What's been bothering you lately?",
];

interface ChatEmptyStateProps {
  onPrompt?: (text: string) => void;
}

export function ChatEmptyState({ onPrompt }: ChatEmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[52vh] gap-6 select-none">
      {/* Name plate */}
      <div className="relative overflow-hidden bg-background/[0.28] backdrop-blur-[40px] shadow-[0_4px_28px_rgba(0,0,0,0.30)] px-8 py-5 flex flex-col items-center gap-2">
        <div className={`absolute inset-0 pointer-events-none opacity-50 ${scanLines}`} />

        {/* Pulsing accent dot */}
        <div className="relative flex items-center gap-2.5">
          <span className="w-1.5 h-1.5 bg-accent rounded-full animate-pulse" />
          <span className="font-mono text-[11px] tracking-[0.35em] uppercase text-accent/80">
            Anima
          </span>
        </div>

        <p className="font-mono text-[9px] tracking-[0.2em] text-foreground/30 uppercase">
          online · ready
        </p>
      </div>

      {/* Conversation starters */}
      <div className="flex flex-col items-center gap-1.5">
        {STARTERS.map((starter) => (
          <button
            key={starter}
            onClick={() => onPrompt?.(starter)}
            className="relative overflow-hidden bg-background/20 backdrop-blur-[24px] shadow-[0_2px_12px_rgba(0,0,0,0.20)] px-4 py-2 text-foreground/45 hover:text-foreground/80 font-mono text-[10px] tracking-[0.1em] transition-all duration-150 hover:bg-background/[0.32] hover:shadow-[0_4px_20px_rgba(0,0,0,0.28)]"
          >
            <div className={`absolute inset-0 pointer-events-none opacity-30 ${scanLines}`} />
            <span className="relative">{starter}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
