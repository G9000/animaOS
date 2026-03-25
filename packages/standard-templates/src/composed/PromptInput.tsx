import { useState, useRef } from "react";
import { cn } from "../utils/cn";
import { Button } from "../primitives/Button";
import { AttachMenu } from "./AttachMenu";
import { MicIcon, SendIcon } from "../icons";

export interface PromptInputProps {
  agentName?: string;
  onSubmit: (value: string) => void;
  className?: string;
}

const MAX_ROWS = 6;

export function PromptInput({ agentName = "Anima", onSubmit, className }: PromptInputProps) {
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const autoResize = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 20 * MAX_ROWS)}px`;
  };

  const submit = () => {
    const v = input.trim();
    if (!v) return;
    onSubmit(v);
    setInput("");
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
        <AttachMenu />

        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => { setInput(e.target.value); autoResize(); }}
          onKeyDown={handleKeyDown}
          placeholder={`talk to ${agentName}...`}
          rows={1}
          className="flex-1 bg-transparent text-body text-foreground font-mono placeholder:text-foreground/15 outline-none resize-none pb-1 leading-6"
        />

        <div className="flex items-center shrink-0">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            iconOnly
            icon={<MicIcon />}
            className="opacity-25 hover:opacity-60"
          />
          {input.trim() && (
            <Button
              type="submit"
              variant="ghost"
              size="sm"
              iconOnly
              icon={<SendIcon />}
              className="opacity-50 hover:opacity-90 animate-fade-in"
            />
          )}
        </div>
      </div>

      <div className="h-px bg-foreground/5 mt-1" />
    </form>
  );
}
