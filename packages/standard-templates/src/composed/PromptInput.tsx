import { useState, useRef, useEffect } from "react";
import EmojiPicker, { type EmojiClickData, Theme } from "emoji-picker-react";
import { cn } from "../utils/cn";
import { Button } from "../primitives/Button";
import { AttachMenu } from "./AttachMenu";
import { MicIcon, SendIcon } from "../icons";

export interface PromptInputProps {
  agentName?: string;
  value?: string;
  onChange?: (value: string) => void;
  onSubmit: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  showAttach?: boolean;
  showMic?: boolean;
  size?: "default" | "lg";
  canSubmit?: boolean;
  onAttach?: (type: string) => void;
}

const MAX_ROWS = 6;

function EmojiIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <path d="M8 14s1.5 2 4 2 4-2 4-2" />
      <line x1="9" y1="9" x2="9.01" y2="9" />
      <line x1="15" y1="9" x2="15.01" y2="9" />
    </svg>
  );
}

export function PromptInput({
  agentName = "Anima",
  value: controlledValue,
  onChange,
  onSubmit,
  disabled = false,
  placeholder,
  className,
  showAttach = true,
  showMic = true,
  size = "default",
  canSubmit = false,
  onAttach,
}: PromptInputProps) {
  const [internalValue, setInternalValue] = useState("");
  const [showEmoji, setShowEmoji] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const isControlled = controlledValue !== undefined;
  const value = isControlled ? controlledValue : internalValue;
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const emojiRef = useRef<HTMLDivElement>(null);
  const recognitionRef = useRef<any>(null);

  useEffect(() => {
    return () => { recognitionRef.current?.abort(); };
  }, []);

  const toggleListening = () => {
    if (isListening) {
      recognitionRef.current?.stop();
      return;
    }
    const SR = (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition;
    if (!SR) return;
    const rec = new SR();
    rec.continuous = false;
    rec.interimResults = false;
    rec.lang = "en-US";
    rec.onstart = () => setIsListening(true);
    rec.onend = () => setIsListening(false);
    rec.onerror = () => setIsListening(false);
    rec.onresult = (e: any) => {
      const transcript = Array.from(e.results as any[])
        .map((r: any) => r[0].transcript)
        .join(" ")
        .trim();
      if (!transcript) return;
      const newValue = value ? `${value} ${transcript}` : transcript;
      if (!isControlled) setInternalValue(newValue);
      onChange?.(newValue);
      setTimeout(autoResize, 0);
    };
    recognitionRef.current = rec;
    rec.start();
  };

  useEffect(() => {
    if (!showEmoji) return;
    const handler = (e: MouseEvent) => {
      if (emojiRef.current && !emojiRef.current.contains(e.target as Node)) {
        setShowEmoji(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showEmoji]);

  const autoResize = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 20 * MAX_ROWS)}px`;
  };

  const insertEmoji = (emojiData: EmojiClickData) => {
    const el = textareaRef.current;
    const emoji = emojiData.emoji;
    const start = el?.selectionStart ?? value.length;
    const end = el?.selectionEnd ?? value.length;
    const newValue = value.slice(0, start) + emoji + value.slice(end);
    if (!isControlled) setInternalValue(newValue);
    onChange?.(newValue);
    setShowEmoji(false);
    setTimeout(() => {
      if (!el) return;
      el.focus();
      el.selectionStart = start + emoji.length;
      el.selectionEnd = start + emoji.length;
      autoResize();
    }, 0);
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newValue = e.target.value;
    if (!isControlled) setInternalValue(newValue);
    onChange?.(newValue);
    autoResize();
  };

  const submit = () => {
    const v = value.trim();
    if ((!v && !canSubmit) || disabled) return;
    onSubmit(v);
    if (!isControlled) setInternalValue("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const hasContent = Boolean(value.trim()) || canSubmit;

  if (size === "lg") {
    return (
      <form onSubmit={(e) => { e.preventDefault(); submit(); }} className={cn("w-full relative", className)}>
        <div
          className={cn(
            "flex flex-col bg-card border transition-all duration-200",
            "border-border/60 hover:border-border",
            "focus-within:border-accent/50 focus-within:ring-2 focus-within:ring-accent/[0.08]",
            "shadow-sm focus-within:shadow-md",
            disabled && "opacity-60",
          )}
        >
          <div className="px-4 pt-3 pb-1">
            <textarea
              ref={textareaRef}
              value={value}
              onChange={handleChange}
              onKeyDown={handleKeyDown}
              placeholder={placeholder ?? `Say something to ${agentName}...`}
              disabled={disabled}
              rows={1}
              className="w-full bg-transparent text-sm text-foreground placeholder:text-muted-foreground/30 outline-none resize-none leading-relaxed"
            />
          </div>

          <div className="flex items-center px-3 pb-2 gap-1 border-t border-border/30 pt-2 mt-1">
            {showAttach && <AttachMenu onAttach={onAttach} />}

            {/* Emoji button */}
            <div ref={emojiRef} className="relative">
              <button
                type="button"
                onClick={() => setShowEmoji((v) => !v)}
                className={cn(
                  "w-8 h-8 flex items-center justify-center transition-all duration-150",
                  showEmoji
                    ? "text-accent"
                    : "text-muted-foreground/40 hover:text-muted-foreground/80"
                )}
              >
                <EmojiIcon />
              </button>
              {showEmoji && (
                <div className="absolute bottom-full mb-2 left-0 z-50 shadow-xl">
                  <EmojiPicker
                    onEmojiClick={insertEmoji}
                    theme={Theme.AUTO}
                    skinTonesDisabled
                    searchDisabled={false}
                    width={320}
                    height={380}
                    previewConfig={{ showPreview: false }}
                  />
                </div>
              )}
            </div>

            {showMic && (
              <Button
                type="button"
                variant={isListening ? "accent" : "ghost"}
                size="sm"
                iconOnly
                icon={<MicIcon />}
                onClick={toggleListening}
                className={isListening ? "animate-pulse" : "opacity-35 hover:opacity-70 transition-opacity"}
              />
            )}

            <div className="ml-auto flex items-center gap-3">
              {value.length > 0 && (
                <span className="font-mono text-[10px] tabular-nums text-muted-foreground/35 select-none">
                  {value.length}
                </span>
              )}
              <Button
                type="submit"
                variant="accent"
                size="sm"
                icon={<SendIcon />}
                iconPosition="right"
                disabled={disabled || !hasContent}
                className={cn(
                  "transition-all duration-150 px-4",
                  hasContent && !disabled ? "opacity-100" : "opacity-30",
                )}
              >
                {disabled ? "···" : "Send"}
              </Button>
            </div>
          </div>
        </div>
      </form>
    );
  }

  return (
    <form onSubmit={(e) => { e.preventDefault(); submit(); }} className={cn("w-full relative", className)}>
      <div
        className={cn(
          "flex flex-col",
          "bg-background/25 backdrop-blur-[40px]",
          "border border-foreground/[0.08]",
          "shadow-[0_20px_50px_-12px_rgba(0,0,0,0.28)]",
          "transition-all duration-200",
          "focus-within:border-foreground/[0.18]",
          disabled && "opacity-50",
        )}
      >
        {/* Text area */}
        <div className="px-4 pt-3 pb-2">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder={placeholder || `talk to ${agentName}...`}
            disabled={disabled}
            rows={1}
            className="w-full bg-transparent text-sm text-foreground/90 placeholder:text-foreground/25 outline-none resize-none leading-relaxed"
          />
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-1 px-2.5 pb-2.5">
          {showAttach && <AttachMenu onAttach={onAttach} />}

          <div className="ml-auto flex items-center gap-1.5">
            {value.length > 0 && (
              <span className="font-mono text-[9px] tabular-nums text-foreground/20 select-none mr-1">
                {value.length}
              </span>
            )}
            {showMic && (
              <button
                type="button"
                onClick={toggleListening}
                className={cn(
                  "size-7 flex items-center justify-center border transition-all duration-150",
                  isListening
                    ? "border-accent bg-accent text-accent-foreground animate-pulse"
                    : "border-foreground/[0.08] text-foreground/20 hover:text-foreground/60 hover:border-foreground/[0.14]",
                )}
              >
                <MicIcon size="sm" />
              </button>
            )}
            <button
              type="submit"
              disabled={disabled || !hasContent}
              className={cn(
                "size-7 flex items-center justify-center transition-all duration-150 border",
                hasContent && !disabled
                  ? "bg-accent border-accent text-accent-foreground hover:bg-accent/90"
                  : "border-foreground/[0.08] text-foreground/20 cursor-not-allowed",
              )}
            >
              <SendIcon size="sm" />
            </button>
          </div>
        </div>
      </div>
    </form>
  );
}
