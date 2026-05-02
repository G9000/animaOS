import { forwardRef, type ComponentProps } from "react";
import { cn } from "../utils/cn";

export type TextareaProps = ComponentProps<"textarea">;

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, rows = 4, ...props }, ref) => {
    return (
      <textarea
        ref={ref}
        rows={rows}
        className={cn(
          "w-full border border-border bg-input px-4 py-3 text-sm text-foreground font-mono rounded-none outline-none transition-all",
          "focus:border-muted-foreground/50 focus:",
          "placeholder:text-muted-foreground/40 placeholder:font-mono",
          "disabled:opacity-50 disabled:cursor-not-allowed",
          "resize-y min-h-[80px]",
          className,
        )}
        {...props}
      />
    );
  },
);

Textarea.displayName = "Textarea";
