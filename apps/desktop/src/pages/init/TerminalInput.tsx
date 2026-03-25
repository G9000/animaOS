import { useState } from "react";
import type { RefObject } from "react";
import { cn, type GlowChar } from "@anima/standard-templates";

function EyeIcon({ open }: { open: boolean }) {
  return open ? (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z"/>
      <circle cx="12" cy="12" r="3"/>
    </svg>
  ) : (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-10-7-10-7a18.45 18.45 0 0 1 5.06-5.94"/>
      <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 10 7 10 7a18.5 18.5 0 0 1-2.16 3.19"/>
      <line x1="1" y1="1" x2="23" y2="23"/>
    </svg>
  );
}

interface TerminalInputProps {
  inputRef: RefObject<HTMLInputElement | null>;
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onBack: () => void;
  onFocus: () => void;
  onBlur: () => void;
  placeholder: string;
  password?: boolean;
  disabled?: boolean;
  isFocused: boolean;
  glowLine: GlowChar[];
}

export function TerminalInput({
  inputRef,
  value,
  onChange,
  onSubmit,
  onBack,
  onFocus,
  onBlur,
  placeholder,
  password,
  disabled,
  isFocused,
  glowLine,
}: TerminalInputProps) {
  const [showPassword, setShowPassword] = useState(false);

  return (
    <div className="w-full max-w-sm mx-auto flex flex-col items-center gap-0">
      <div className="relative w-full">
        <input
          ref={inputRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onSubmit();
            if (e.key === "Escape") onBack();
          }}
          onFocus={onFocus}
          onBlur={onBlur}
          type={password && !showPassword ? "password" : "text"}
          placeholder={placeholder}
          disabled={disabled}
          spellCheck={false}
          autoComplete="off"
          className="w-full bg-transparent outline-none font-mono text-ui text-center text-muted-foreground placeholder:text-subtle-foreground caret-transparent pb-2 [&::-ms-reveal]:hidden [&::-ms-clear]:hidden"
        />
        {password && (
          <button
            type="button"
            tabIndex={-1}
            onClick={() => setShowPassword((s) => !s)}
            className="absolute right-0 top-0 h-full flex items-center px-1 text-subtle-foreground hover:text-muted-foreground transition-colors"
          >
            <EyeIcon open={showPassword} />
          </button>
        )}
      </div>
      <pre className="text-caption leading-none whitespace-pre select-none w-full text-center">
        {glowLine.map((g, i) => (
          <span
            key={i}
            className={cn(
              isFocused
                ? g.bright ? "text-foreground/70" : "text-foreground/30"
                : "text-foreground/10",
            )}
          >
            {g.ch}
          </span>
        ))}
      </pre>
    </div>
  );
}
