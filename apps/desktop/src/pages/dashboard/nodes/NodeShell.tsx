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
  onClose: () => void;
  actions?: NodeAction[];
  className?: string;
  hideHeader?: boolean;
}

export function NodeShell({
  title,
  headerExtra,
  media,
  children,
  footer,
  onClose,
  actions = [],
  className = "",
  hideHeader = false,
}: NodeShellProps) {
  const [expanded, setExpanded] = useState(false);
  const visibleActions = actions.slice(0, 2);
  const hiddenActions = actions.slice(2);

  return (
    <div
      className={cn("group relative bg-card border border-border/50 shadow-md rounded-xl overflow-visible", className)}
    >
      {/* Floating close button — lives outside the clipped inner wrapper */}
      <button
        onClick={onClose}
        className="absolute -top-7 right-1 z-20 w-5 h-5 flex items-center justify-center rounded-full bg-card/90 border border-border/50 text-muted-foreground/40 hover:text-foreground hover:bg-card opacity-0 group-hover:opacity-100 transition-all duration-150"
        aria-label="Close widget"
      >
        <span className="text-xs leading-none">×</span>
      </button>

      {/* Overflow actions dropdown — outside the clipped wrapper so it isn't cut off */}
      {hiddenActions.length > 0 && expanded && (
        <div className="absolute right-3 top-10 z-30 bg-card border border-border/50 shadow-lg rounded-xl min-w-[140px] overflow-hidden">
          {hiddenActions.map((action) => (
            <button
              key={action.id}
              onClick={() => {
                action.onClick();
                setExpanded(false);
              }}
              className="w-full text-left px-3 py-2 text-xs text-foreground/70 hover:bg-secondary transition-colors flex items-center gap-2"
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
      <div className="overflow-hidden rounded-xl">

      {!hideHeader && (
        <div className="px-3 py-2 flex items-center justify-between border-b border-border/30 bg-card">
          <div className="min-w-0 flex items-center gap-2 pr-6">
            {typeof title === "string" ? (
              <span className="font-mono text-[9px] tracking-[0.22em] uppercase text-muted-foreground/55 truncate block">
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
                className="px-1.5 py-0.5 text-muted-foreground/40 hover:text-foreground transition-colors"
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

      {media && <div className="relative">{media}</div>}

      {children}

      {footer && (
        <div className="border-t border-border/20">{footer}</div>
      )}

      </div>{/* end inner overflow-hidden wrapper */}
    </div>
  );
}
