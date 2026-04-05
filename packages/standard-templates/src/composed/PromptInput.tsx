import { useState, useRef } from "react";
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
}

const MAX_ROWS = 6;

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
}: PromptInputProps) {
  const [internalValue, setInternalValue] = useState("");
  const isControlled = controlledValue !== undefined;
  const value = isControlled ? controlledValue : internalValue;
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const autoResize = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 20 * MAX_ROWS)}px`;
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newValue = e.target.value;
    if (!isControlled) {
      setInternalValue(newValue);
    }
    onChange?.(newValue);
    autoResize();
  };

  const submit = () => {
    const v = value.trim();
    if (!v || disabled) return;
    onSubmit(v);
    if (!isControlled) {
      setInternalValue("");
    }
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form onSubmit={(e) => { e.preventDefault(); submit(); }} className={cn("w-full", className)}>
      <div className="flex items-end gap-2">
        {showAttach && <AttachMenu />}

        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholder || `talk to ${agentName}...`}
          disabled={disabled}
          rows={1}
          className="flex-1 bg-transparent text-body text-foreground font-mono placeholder:text-foreground/15 outline-none resize-none pb-1 leading-6"
        />

        <div className="flex items-center shrink-0">
          {showMic && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              iconOnly
              icon={<MicIcon />}
              className="opacity-25 hover:opacity-60"
            />
          )}
          {(value.trim() || disabled) && (
            <Button
              type="submit"
              variant="ghost"
              size="sm"
              iconOnly
              icon={<SendIcon />}
              disabled={disabled || !value.trim()}
              className="opacity-50 hover:opacity-90 animate-fade-in disabled:opacity-20"
            />
          )}
        </div>
      </div>

      <div className="h-px bg-foreground/5 mt-1" />
    </form>
  );
}
