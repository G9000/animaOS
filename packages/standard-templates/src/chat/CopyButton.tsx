"use client";

import { useState } from "react";
import { CopyIcon, CheckIcon } from "./icons";
import { cn } from "../utils/cn";

export interface CopyButtonProps {
  text: string;
  className?: string;
  copiedDuration?: number;
}

export function CopyButton({
  text,
  className,
  copiedDuration = 1500,
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), copiedDuration);
    } catch {
      // Silently fail
    }
  };

  return (
    <button
      onClick={handleCopy}
      className={cn(
        "p-1.5 text-muted-foreground/40 hover:text-muted-foreground hover:bg-muted-foreground/10 transition-all",
        className,
      )}
      title="Copy to clipboard"
      aria-label={copied ? "Copied" : "Copy to clipboard"}
    >
      {copied ? (
        <CheckIcon className="w-3.5 h-3.5" />
      ) : (
        <CopyIcon className="w-3.5 h-3.5" />
      )}
    </button>
  );
}
