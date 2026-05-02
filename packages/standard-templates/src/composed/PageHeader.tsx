import { type ReactNode } from "react";
import { cn } from "../utils/cn";

export interface PageHeaderProps {
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

export function PageHeader({ title, meta, actions, className }: PageHeaderProps) {
  return (
    <div className={cn("px-5 py-3 border-b-2 border-border bg-card/40", className)}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="font-mono text-[10px] text-muted-foreground tracking-[0.22em] uppercase">
            {title}
          </span>
          {meta && (
            <>
              <div className="w-1 h-1 rounded-none bg-border" />
              {meta}
            </>
          )}
        </div>
        {actions && (
          <div className="flex items-center gap-2">{actions}</div>
        )}
      </div>
    </div>
  );
}
