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
          "w-9 h-5 rounded-none border transition-all relative",
          enabled
            ? "bg-accent/20 border-accent"
            : "bg-input border-border",
          disabled && "opacity-50 cursor-not-allowed",
          className,
        )}
      >
        <div
          className={cn(
            "absolute top-[2px] w-3.5 h-3.5 rounded-none border transition-all",
            enabled
              ? "left-[14px] bg-accent border-accent"
              : "left-[2px] bg-muted-foreground/30 border-transparent",
          )}
        />
      </button>
    );
  },
);

Toggle.displayName = "Toggle";
