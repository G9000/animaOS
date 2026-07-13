import { useRef, useState, useCallback, type ReactNode } from "react";
import { NodeShell, type NodeShellProps } from "./NodeShell";

export interface ListShellProps extends Omit<NodeShellProps, "fluid" | "children"> {
  children: ReactNode;
  emptyState?: ReactNode;
}

export function ListShell({ children, emptyState, ...shellProps }: ListShellProps) {
  const outerRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const [scrollY, setScrollY] = useState(0);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.stopPropagation();
    const outer = outerRef.current;
    const inner = innerRef.current;
    if (!outer || !inner) return;
    const maxScroll = Math.max(0, inner.offsetHeight - outer.clientHeight);
    setScrollY((prev) => Math.max(0, Math.min(maxScroll, prev + e.deltaY)));
  }, []);

  return (
    <NodeShell fluid {...shellProps}>
      <div
        ref={outerRef}
        className="h-full relative nowheel"
        style={{ clipPath: "inset(0 -20px 0 0)" }}
        onWheel={handleWheel}
      >
        {emptyState ? (
          <div className="h-full flex items-center justify-center">
            {emptyState}
          </div>
        ) : (
          <div
            ref={innerRef}
            className="absolute left-0 right-0 top-0"
            style={{ transform: `translateY(-${scrollY}px)` }}
          >
            {children}
          </div>
        )}
      </div>
    </NodeShell>
  );
}
