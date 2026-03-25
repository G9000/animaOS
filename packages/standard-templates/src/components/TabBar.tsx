import { cn } from "../utils/cn";

export interface Tab {
  key: string;
  label: string;
  count?: number;
}

export interface TabBarProps {
  tabs: Tab[];
  active: string;
  onSelect: (key: string) => void;
  className?: string;
}

export function TabBar({ tabs, active, onSelect, className }: TabBarProps) {
  return (
    <div className={cn("px-5 py-1.5 border-b border-border flex gap-px", className)}>
      {tabs.map((t) => (
        <button
          key={t.key}
          onClick={() => onSelect(t.key)}
          className={cn(
            "font-mono text-[9px] px-2.5 py-1.5 tracking-wider transition-colors",
            active === t.key
              ? "bg-primary/[0.08] text-primary border-b-2 border-primary"
              : "text-muted-foreground/50 hover:text-muted-foreground",
          )}
        >
          {t.label}
          {t.count !== undefined && (
            <span className="ml-1 text-muted-foreground/30">{t.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}
