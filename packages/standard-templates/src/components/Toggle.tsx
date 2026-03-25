import { cn } from "../utils/cn";

export interface ToggleProps {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  className?: string;
}

export function Toggle({ enabled, onChange, className }: ToggleProps) {
  return (
    <button
      type="button"
      onClick={() => onChange(!enabled)}
      className={cn(
        "w-7 h-4 rounded-full transition-colors relative",
        enabled ? "bg-primary/30" : "bg-input",
        className,
      )}
    >
      <div
        className={cn(
          "absolute top-0.5 w-3 h-3 rounded-full transition-all",
          enabled ? "left-3.5 bg-primary" : "left-0.5 bg-muted-foreground/30",
        )}
      />
    </button>
  );
}
