import { type ReactNode } from "react";
import { cn } from "../utils/cn";

const variants = {
  error: "bg-destructive/5 border-l-2 border-destructive text-destructive",
  warning: "bg-warning/5 border-l-2 border-warning text-warning",
  info: "bg-primary/5 border-l-2 border-primary text-primary",
} as const;

export type AlertVariant = keyof typeof variants;

export interface AlertProps {
  variant?: AlertVariant;
  children: ReactNode;
  className?: string;
}

export function Alert({ variant = "error", children, className }: AlertProps) {
  return (
    <div className={cn("px-3.5 py-2.5 font-mono text-[10px] tracking-wider", variants[variant], className)}>
      {children}
    </div>
  );
}
