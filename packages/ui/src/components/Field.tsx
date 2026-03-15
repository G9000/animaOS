import { useId, type ComponentProps } from "react";
import { Input } from "./Input";
import { Label } from "./Label";

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
        className="absolute top-0 left-3 z-10 px-1.5 py-0.5 text-[10px] uppercase tracking-widest text-bg bg-text-muted leading-none"
      >
        {label}
      </Label>
      <Input
        id={fieldId}
        className="pt-4 focus:border-primary/35 focus:bg-[#1a1a1f]"
        {...inputProps}
      />
      {hint && <p className="mt-2 text-[11px] text-text-muted">{hint}</p>}
    </div>
  );
}
