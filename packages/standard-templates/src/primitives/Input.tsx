import { type ComponentProps } from "react";
import { cn } from "../utils/cn";

export type InputProps = ComponentProps<"input">;

export function Input({ className, ...props }: InputProps) {
  return (
    <input
      className={cn(
        "w-full border border-border bg-input px-3.5 py-2.5 text-sm text-foreground outline-none transition-colors focus:border-muted-foreground/40 placeholder:text-muted-foreground/30",
        className,
      )}
      {...props}
    />
  );
}
