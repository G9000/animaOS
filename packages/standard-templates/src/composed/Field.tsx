import { useId, type ComponentProps } from "react";
import { Input } from "../primitives/Input";
import { Label } from "../primitives/Label";

export interface FieldProps extends ComponentProps<"input"> {
  /** Text shown above the input. */
  label: string;
  /** Optional hint shown below the input. */
  hint?: string;
  /** Error message displayed below the input. */
  error?: string;
  /** Additional classes for the container wrapper. */
  containerClassName?: string;
}

export function Field({
  label,
  hint,
  error,
  id,
  className,
  containerClassName,
  ...inputProps
}: FieldProps) {
  const autoId = useId();
  const fieldId = id ?? autoId;
  const errorId = `${fieldId}-error`;
  const hintId = `${fieldId}-hint`;

  const hasError = Boolean(error);
  const helperId = hasError ? errorId : hint ? hintId : undefined;

  return (
    <div className={cn("space-y-1.5", containerClassName)}>
      <Label
        htmlFor={fieldId}
        className="font-mono text-[9px] uppercase tracking-[0.22em] text-muted-foreground"
      >
        {label}
      </Label>
      <Input
        id={fieldId}
        aria-invalid={hasError}
        aria-describedby={helperId}
        className={cn(
          hasError && "border-destructive focus:border-destructive",
          className,
        )}
        {...inputProps}
      />
      {hasError ? (
        <p id={errorId} className="font-mono text-[9px] tracking-wider text-destructive">
          {error}
        </p>
      ) : hint ? (
        <p id={hintId} className="font-mono text-[9px] tracking-wider text-muted-foreground/60">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

// Simple cn helper since this file doesn't import it
function cn(...classes: Array<string | boolean | undefined>) {
  return classes.filter(Boolean).join(" ");
}
