import { useId, type ComponentProps } from "react";
import { Input } from "../primitives/Input";
import { Label } from "../primitives/Label";

export interface FieldProps extends ComponentProps<"input"> {
  /** Text shown above the input. */
  label: string;
  /** Optional hint shown below the input. */
  hint?: string;
}

export function Field({ label, hint, id, ...inputProps }: FieldProps) {
  const autoId = useId();
  const fieldId = id ?? autoId;

  return (
    <div className="relative pt-2">
      <Label
        htmlFor={fieldId}
        className="absolute top-0 left-3 z-10 px-1.5 py-0.5 text-[10px] uppercase tracking-widest text-background bg-muted-foreground leading-none"
      >
        {label}
      </Label>
      <Input
        id={fieldId}
        className="pt-4 focus:border-primary/35 focus:bg-card"
        {...inputProps}
      />
      {hint && <p className="mt-2 text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}
