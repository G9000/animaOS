import { useState, type ReactNode } from "react";
import { cn } from "@anima/standard-templates"

export interface NodeAction {
  id: string;
  label?: string;
  icon?: ReactNode;
  onClick: () => void;
}

interface NodeShellProps {
  title?: ReactNode;
  headerExtra?: ReactNode;
  media?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  handles?: ReactNode;
  onClose: () => void;
  actions?: NodeAction[];
  className?: string;
  hideHeader?: boolean;
  required?: boolean;
  fluid?: boolean;
}

export function NodeShell({
  title,
  headerExtra,
  media,
  children,
  footer,
  handles,
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
      className={cn("group relative bg-background/20 backdrop-blur-[36px] border border-foreground/[0.08] shadow-[0_6px_32px_rgba(0,0,0,0.22)] rounded-xl overflow-visible", fluid && "h-full", className)}
    >
      {/* Floating close button — hidden for required nodes */}
      {!required && (
        <button
          onClick={onClose}
          className="absolute -top-5 right-0 z-20 h-4 px-1.5 flex items-center rounded-sm bg-background/60 border border-foreground/[0.07] font-mono text-[8px] text-foreground/25 hover:text-foreground/60 hover:bg-background/80 opacity-0 group-hover:opacity-100 transition-all duration-200 backdrop-blur-sm"
          aria-label="Close widget"
        >
          ×
        </button>
      )}

      {/* Connection handles — positioned relative to the outer div, not clipped */}
      {handles}

      {/* Overflow actions dropdown — outside the clipped wrapper so it isn't cut off */}
      {hiddenActions.length > 0 && expanded && (
        <div className="absolute right-3 top-10 z-30 bg-background/80 backdrop-blur-[24px] border border-foreground/[0.08] shadow-lg rounded-xl min-w-[140px] overflow-hidden">
          {hiddenActions.map((action) => (
            <button
              key={action.id}
              onClick={() => {
                action.onClick();
                setExpanded(false);
              }}
              className="w-full text-left px-3 py-2 text-xs text-foreground/60 hover:text-foreground hover:bg-foreground/[0.05] transition-colors flex items-center gap-2"
            >
              {action.icon && <span className="text-xs">{action.icon}</span>}
              <span className="font-mono text-[9px] tracking-wider uppercase">
                {action.label}
              </span>
            </button>
          ))}
        </div>
      )}

      {/* Inner wrapper clips children (images etc.) to the card's rounded corners */}
      <div className={cn("overflow-hidden rounded-xl", fluid && "h-full flex flex-col")}>

      {!hideHeader && (
        <div className="px-3.5 h-9 flex items-center justify-between border-b border-foreground/[0.06]">
          <div className="min-w-0 flex items-center gap-2 pr-4">
            {typeof title === "string" ? (
              <span className="font-mono text-[8px] tracking-[0.3em] uppercase text-foreground/30 truncate block">
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
                className="px-1.5 py-0.5 text-foreground/25 hover:text-foreground/70 transition-colors"
                title={action.label}
              >
                {action.icon ? (
                  <span className="text-xs">{action.icon}</span>
                ) : (
                  <span className="font-mono text-[8px] tracking-wider uppercase">
                    {action.label}
                  </span>
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

      {footer && (
        <div className="border-t border-foreground/[0.05] shrink-0">{footer}</div>
      )}

      </div>
    </div>
  );
}
