import { useState, type ReactNode } from "react";
import { cn } from "../utils/cn";
import { XIcon } from "../icons/XIcon";

export interface NodeAction {
  id: string;
  label?: string;
  icon?: ReactNode;
  onClick: () => void;
}

export interface NodeShellProps {
  title?: ReactNode;
  icon?: ReactNode;
  headerExtra?: ReactNode;
  media?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  handles?: ReactNode;
  topActions?: ReactNode;
  onClose: () => void;
  actions?: NodeAction[];
  className?: string;
  hideHeader?: boolean;
  required?: boolean;
  fluid?: boolean;
}

export function NodeShell({
  title,
  icon,
  headerExtra,
  media,
  children,
  footer,
  handles,
  topActions,
  onClose,
  actions = [],
  className = "",
  hideHeader = false,
  required = false,
  fluid = false,
}: NodeShellProps) {
  const [expanded, setExpanded] = useState(false);
  const visibleActions = actions.slice(0, 2);
  const hiddenActions = actions.slice(2);

  return (
    <div
      className={cn(
        "group relative bg-background/20 backdrop-blur-[36px] border border-accent/20 shadow-[0_6px_32px_rgba(0,0,0,0.22)] overflow-visible",
        fluid && "h-full",
        className,
      )}
    >
      {(!required || topActions) && (
        <div
          className="absolute -top-8 right-0 z-20 flex items-stretch h-6 opacity-0 group-hover:opacity-100 transition-all duration-200"
          style={{
            background: "color-mix(in oklch, var(--color-background) 90%, var(--color-accent) 10%)",
            clipPath: "polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)",
            filter: "drop-shadow(0 0 6px color-mix(in oklch, var(--color-accent) 60%, transparent))",
          }}
        >
          {topActions}
          {!required && (
            <button
              onClick={onClose}
              className="px-2 flex items-center text-muted-foreground/50 hover:text-foreground hover:bg-accent transition-colors"
              aria-label="Close widget"
            >
              <XIcon size="sm" strokeWidth={2} />
            </button>
          )}
        </div>
      )}

      {handles}

      {hiddenActions.length > 0 && expanded && (
        <div className="absolute right-3 top-10 z-30 bg-background/80 backdrop-blur-[24px] border border-hairline shadow-lg rounded-xl min-w-[140px] overflow-hidden">
          {hiddenActions.map((action) => (
            <button
              key={action.id}
              onClick={() => { action.onClick(); setExpanded(false); }}
              className="w-full text-left px-3 py-2 text-xs text-foreground/60 hover:text-foreground hover:bg-foreground/[0.05] transition-colors flex items-center gap-2"
            >
              {action.icon && <span className="text-xs">{action.icon}</span>}
              <span className="font-mono text-label tracking-wider uppercase">{action.label}</span>
            </button>
          ))}
        </div>
      )}

      <div className={cn(!fluid && "overflow-hidden", fluid && "h-full flex flex-col overflow-visible")}>
        {!hideHeader && (
          <div className="px-3.5 h-9 flex items-center justify-between bg-accent/10 border-b border-hairline-faint">
            <div className="min-w-0 flex items-center gap-1.5 pr-4">
              {icon}
              {typeof title === "string" ? (
                <span className="font-mono font-semibold text-label uppercase text-foreground truncate block">
                  {title}
                </span>
              ) : (
                <div className="min-w-0">{title}</div>
              )}
              {headerExtra}
            </div>
            <div className="flex items-center gap-1 shrink-0">
              {visibleActions.map((action) => (
                <button
                  key={action.id}
                  onClick={action.onClick}
                  className="group font-semibold transition-colors"
                  title={action.label}
                >
                  {action.icon ? (
                    <span className="text-xs">{action.icon}</span>
                  ) : (
                    <span className="px-1 hover:bg-accent text-foreground/25 hover:text-foreground font-mono text-micro tracking-wider uppercase">{action.label}</span>
                  )}
                </button>
              ))}
              {hiddenActions.length > 0 && (
                <button
                  onClick={() => setExpanded((v) => !v)}
                  className="px-1.5 py-0.5 text-muted-foreground/40 hover:text-foreground transition-colors"
                  aria-label="More actions"
                >
                  <span className="font-mono text-xs">⋯</span>
                </button>
              )}
            </div>
          </div>
        )}

        {media && <div className="relative shrink-0">{media}</div>}

        {fluid ? <div className="flex-1 min-h-0">{children}</div> : children}

        {footer && <div className="border-t border-hairline-faint shrink-0">{footer}</div>}
      </div>
    </div>
  );
}
