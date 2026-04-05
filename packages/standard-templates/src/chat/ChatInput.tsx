"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { cn } from "../utils/cn";

export interface ChatInputProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  disabled?: boolean;
  placeholder?: string;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  className?: string;
}

const MAX_ROWS = 6;

export function ChatInput({
  value,
  onChange,
  onSubmit,
  disabled = false,
  placeholder = "type something...",
  collapsed = false,
  onToggleCollapse,
  className,
}: ChatInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [isFocused, setIsFocused] = useState(false);

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const newHeight = Math.min(el.scrollHeight, 24 * MAX_ROWS);
    el.style.height = `${newHeight}px`;
  }, [value]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (!disabled && value.trim()) {
          onSubmit();
        }
      }
    },
    [disabled, value, onSubmit],
  );

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      onChange(e.target.value);
    },
    [onChange],
  );

  const canSubmit = value.trim() && !disabled;

  return (
    <div className={cn("flex flex-col", className)}>
      {/* Collapse toggle bar */}
      {onToggleCollapse && (
        <button
          onClick={onToggleCollapse}
          className="w-full flex items-center justify-between px-4 py-1.5 group hover:bg-card/80 transition-colors"
        >
          <span className="font-mono text-[9px] tracking-widest text-muted-foreground/30 group-hover:text-muted-foreground/60 transition-colors">
            INPUT
          </span>
          <span className="font-mono text-[9px] text-muted-foreground/25 group-hover:text-muted-foreground/50 transition-colors">
            {collapsed ? "▲" : "▼"}
          </span>
        </button>
      )}

      {/* Input area */}
      <div
        className={cn(
          "transition-all duration-200 overflow-hidden",
          collapsed ? "max-h-0 opacity-0" : "max-h-96 opacity-100",
        )}
      >
        <div className="px-4 pb-4">
          <div
            className={cn(
              "flex gap-3 items-end border bg-card px-4 py-3 transition-all duration-150",
              isFocused
                ? "border-primary/60 ring-1 ring-primary/20"
                : "border-border hover:border-primary/40",
              disabled && "opacity-60",
            )}
          >
            {/* Prompt indicator */}
            <div className="font-mono text-[11px] text-primary/50 pt-1 select-none shrink-0 tracking-wider">
              &gt;_
            </div>

            {/* Textarea */}
            <textarea
              ref={textareaRef}
              value={value}
              onChange={handleChange}
              onKeyDown={handleKeyDown}
              onFocus={() => setIsFocused(true)}
              onBlur={() => setIsFocused(false)}
              placeholder={placeholder}
              disabled={disabled}
              rows={1}
              className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground/30 outline-none resize-none max-h-40 py-0.5 leading-relaxed"
            />

            {/* Send button */}
            <button
              onClick={onSubmit}
              disabled={!canSubmit}
              className={cn(
                "font-mono text-[10px] tracking-widest px-3 py-1.5 border transition-all duration-150 shrink-0 self-end",
                canSubmit
                  ? "border-primary/60 text-primary hover:bg-primary/5"
                  : "border-border text-muted-foreground/30",
              )}
            >
              SEND
            </button>
          </div>

          {/* Helper text */}
          <div className="mt-1.5 px-1 flex items-center justify-between">
            <span className="font-mono text-[9px] text-muted-foreground/25 tracking-wider">
              ENTER to send · SHIFT+ENTER for newline
            </span>
            {disabled && (
              <span className="font-mono text-[9px] text-primary/40 tracking-wider animate-pulse">
                PROCESSING...
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
