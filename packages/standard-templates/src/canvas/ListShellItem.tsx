import type { ReactNode } from "react";
import { cn } from "../utils/cn";

export interface ListShellItemProps {
  onClick: () => void;
  className?: string;
  children: ReactNode;
}

export function ListShellItem({ onClick, className, children }: ListShellItemProps) {
  return (
    <button
      onClick={onClick}
      className={cn("group/item w-full flex text-left hover:bg-accent", className)}
      style={{ transition: "transform 150ms ease, box-shadow 150ms ease, background-color 150ms ease" }}
      onMouseEnter={(e) => {
        const el = e.currentTarget as HTMLElement;
        el.style.transform = "translateX(6px)";
        el.style.boxShadow = "-2px 2px 0 var(--color-accent-dark), -4px 4px 0 color-mix(in oklch, var(--color-accent-dark) 50%, transparent)";
      }}
      onMouseLeave={(e) => {
        const el = e.currentTarget as HTMLElement;
        el.style.transform = "";
        el.style.boxShadow = "";
      }}
    >
      <div className="flex-1 min-w-0 px-3 py-2.5">
        {children}
      </div>
    </button>
  );
}
