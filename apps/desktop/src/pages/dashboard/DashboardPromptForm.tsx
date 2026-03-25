import type { FormEvent, RefObject } from "react";

interface DashboardPromptFormProps {
  inputRef: RefObject<HTMLInputElement | null>;
  input: string;
  onInputChange: (value: string) => void;
  onSubmit: (e: FormEvent) => void;
}

export function DashboardPromptForm({
  inputRef,
  input,
  onInputChange,
  onSubmit,
}: DashboardPromptFormProps) {
  return (
    <form onSubmit={onSubmit}>
      <div className="flex items-center gap-2 border-b border-text/5 pb-2 focus-within:border-text/15 transition-colors max-w-sm mx-auto">
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={(e) => onInputChange(e.target.value)}
          placeholder="Talk to Anima..."
          className="flex-1 bg-transparent text-sm text-foreground font-sans placeholder:text-foreground/15 outline-none text-center focus:text-left transition-all"
        />
        {input.trim() && (
          <button
            type="submit"
            className="text-foreground/25 hover:text-foreground/50 text-xs font-mono transition-colors shrink-0"
          >
            →
          </button>
        )}
      </div>
    </form>
  );
}
