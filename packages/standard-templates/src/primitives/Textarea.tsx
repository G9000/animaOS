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
          "w-full border border-border bg-input px-3.5 py-2.5 text-sm text-foreground outline-none transition-colors focus:border-muted-foreground/40 placeholder:text-muted-foreground/30 resize-y min-h-[80px]",
          className,
        )}
        {...props}
      />
    );
  },
);

Textarea.displayName = "Textarea";
