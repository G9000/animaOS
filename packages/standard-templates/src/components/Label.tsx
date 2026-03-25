import { type ComponentProps } from "react";
import { cn } from "../utils/cn";

export type LabelProps = ComponentProps<"label">;

export function Label({ className, ...props }: LabelProps) {
  return (
    <label
      className={cn(
        "text-[11px] font-medium tracking-wide text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}
