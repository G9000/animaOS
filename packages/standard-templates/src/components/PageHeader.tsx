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
    <div className={cn("px-5 py-2.5 border-b border-border bg-card/40", className)}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="font-mono text-[10px] text-muted-foreground tracking-wider">
            {title}
          </span>
          {meta && (
            <>
              <div className="w-px h-3 bg-border" />
              {meta}
            </>
          )}
        </div>
        {actions && (
          <div className="flex items-center gap-3">{actions}</div>
        )}
      </div>
    </div>
  );
}
