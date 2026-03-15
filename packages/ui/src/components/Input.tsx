import { type ComponentProps } from "react";
import { cn } from "../utils/cn";

export type InputProps = ComponentProps<"input">;

export function Input({ className, ...props }: InputProps) {
  return (
    <input
      className={cn(
        "w-full border border-border bg-bg-input px-3.5 py-2.5 text-sm text-text outline-none transition-colors focus:border-text-muted/40 placeholder:text-text-muted/30",
        className,
      )}
      {...props}
    />
  );
}
