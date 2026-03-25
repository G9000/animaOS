import { cn } from "../utils/cn";

export interface DotLoaderProps {
  className?: string;
}

export function DotLoader({ className }: DotLoaderProps) {
  return (
    <div className={cn("flex gap-1.5 justify-center items-center", className)}>
      <span className="w-1 h-1 bg-subtle-foreground animate-pulse" />
      <span className="w-1 h-1 bg-subtle-foreground animate-pulse [animation-delay:150ms]" />
      <span className="w-1 h-1 bg-subtle-foreground animate-pulse [animation-delay:300ms]" />
    </div>
  );
}
