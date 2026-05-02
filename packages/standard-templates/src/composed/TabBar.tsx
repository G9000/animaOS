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
    <div className={cn("px-5 py-2 border-b-2 border-border flex gap-1", className)}>
      {tabs.map((t) => (
        <button
          key={t.key}
          onClick={() => onSelect(t.key)}
          className={cn(
            "font-mono text-[9px] px-3 py-2 tracking-[0.18em] uppercase transition-all rounded-none border",
            active === t.key
              ? "bg-primary text-primary-foreground border-primary "
              : "bg-transparent text-muted-foreground border-transparent hover:text-foreground hover:bg-secondary",
          )}
        >
          {t.label}
          {t.count !== undefined && (
            <span className={cn(
              "ml-1.5 text-[8px]",
              active === t.key ? "text-primary-foreground/60" : "text-muted-foreground/40"
            )}>
              {t.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
