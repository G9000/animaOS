import { cn } from "../utils/cn";

export interface BadgeProps {
  children: string;
  className?: string;
}

export function Badge({ children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        "font-mono text-[8px] px-1.5 py-0.5 bg-input border border-border tracking-wider text-muted-foreground/50",
        className,
      )}
    >
      {children}
    </span>
  );
}
