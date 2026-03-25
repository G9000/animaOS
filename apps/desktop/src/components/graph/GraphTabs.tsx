import type { Tab } from "./constants";

interface GraphTabsProps {
  tab: Tab;
  onTabChange: (tab: Tab) => void;
}

const TABS = [
  { key: "overview", label: "OVERVIEW" },
  { key: "entities", label: "ENTITIES" },
  { key: "paths", label: "PATHS" },
] as const;

export function GraphTabs({ tab, onTabChange }: GraphTabsProps) {
  return (
    <div className="px-5 py-1.5 border-b border-border flex gap-px">
      {TABS.map((t) => (
        <button
          key={t.key}
          onClick={() => onTabChange(t.key)}
          className={`font-mono text-[9px] px-2.5 py-1.5 tracking-wider transition-colors ${
            tab === t.key
              ? "bg-primary/[0.08] text-primary border-b-2 border-primary"
              : "text-muted-foreground/50 hover:text-muted-foreground"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
