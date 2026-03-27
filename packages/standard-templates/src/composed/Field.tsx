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
    <div className={cn("relative pt-2", containerClassName)}>
      <Label
        htmlFor={fieldId}
        className="absolute top-0 left-3 z-10 px-1.5 py-0.5 text-[10px] uppercase tracking-widest text-background bg-muted-foreground leading-none"
      >
        {label}
      </Label>
      <Input
        id={fieldId}
        aria-invalid={hasError}
        aria-describedby={helperId}
        className={cn(
          "pt-4 focus:border-primary/35 focus:bg-card",
          hasError && "border-destructive focus:border-destructive",
          className,
        )}
        {...inputProps}
      />
      {hasError ? (
        <p id={errorId} className="mt-2 text-[11px] text-destructive">
          {error}
        </p>
      ) : hint ? (
        <p id={hintId} className="mt-2 text-[11px] text-muted-foreground">
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
