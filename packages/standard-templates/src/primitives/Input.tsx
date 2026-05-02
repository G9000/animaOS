import { forwardRef, type ComponentProps } from "react";
import { cn } from "../utils/cn";

export type InputProps = ComponentProps<"input">;

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, ...props }, ref) => {
    return (
      <input
        ref={ref}
        className={cn(
          "w-full border border-border bg-input px-4 py-3 text-sm text-foreground font-mono rounded-none outline-none transition-all",
          "placeholder:text-muted-foreground/40 placeholder:font-mono",
          "focus:border-muted-foreground/50 focus:",
          "disabled:opacity-50 disabled:cursor-not-allowed",
          className,
        )}
        {...props}
      />
    );
  },
);

Input.displayName = "Input";
