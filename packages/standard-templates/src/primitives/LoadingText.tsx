import { cn } from "../utils/cn";

export interface LoadingTextProps {
  text?: string;
  className?: string;
}

export function LoadingText({ text = "LOADING...", className }: LoadingTextProps) {
  return (
    <span className={cn("font-mono text-caption text-muted-foreground/30 animate-pulse tracking-wider", className)}>
      {text}
    </span>
  );
}
