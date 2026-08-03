import { type ComponentProps } from "react";
import { cn } from "../utils/cn";

export type LabelProps = ComponentProps<"label">;

export function Label({ className, ...props }: LabelProps) {
  return (
    <label
      className={cn(
        "text-detail font-medium tracking-wide text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}
