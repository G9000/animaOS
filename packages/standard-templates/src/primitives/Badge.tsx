import { cn } from "../utils/cn";

export interface BadgeProps {
  children: string;
  className?: string;
  variant?: "default" | "accent" | "muted";
}

export function Badge({ children, className, variant = "default" }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center font-mono text-[8px] px-2 py-0.5 border rounded-none tracking-[0.2em] uppercase",
        variant === "default" && "bg-input border-border text-muted-foreground",
        variant === "accent" && "bg-accent/10 border-accent/30 text-accent",
        variant === "muted" && "bg-transparent border-border/50 text-muted-foreground/60",
        className,
      )}
    >
      {children}
    </span>
  );
}
