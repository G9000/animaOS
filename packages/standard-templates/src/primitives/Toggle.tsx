import { forwardRef } from "react";
import { cn } from "../utils/cn";

export interface ToggleProps {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  className?: string;
  /** Accessible label for the toggle */
  label?: string;
  /** Whether the toggle is disabled */
  disabled?: boolean;
}

export const Toggle = forwardRef<HTMLButtonElement, ToggleProps>(
  ({ enabled, onChange, className, label, disabled = false }, ref) => {
    return (
      <button
        ref={ref}
        type="button"
        role="switch"
        aria-checked={enabled}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!enabled)}
        className={cn(
          "w-7 h-4 rounded-full transition-colors relative",
          enabled ? "bg-primary/30" : "bg-input",
          disabled && "opacity-50 cursor-not-allowed",
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
  },
);

Toggle.displayName = "Toggle";
