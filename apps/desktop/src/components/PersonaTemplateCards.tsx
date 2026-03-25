import { useState } from "react";
import { cn, Button } from "@anima/standard-templates";

export interface PersonaCardData {
  id: string;
  label: string;
  desc: string;
  relationship: string;
  persona: string;
}

interface PersonaTemplateCardsProps {
  templates: PersonaCardData[];
  onSelect: (template: PersonaCardData) => void;
  onBack?: () => void;
  onHoverChange?: (hovered: boolean) => void;
  disabled?: boolean;
}

export function PersonaTemplateCards({
  templates,
  onSelect,
  onBack,
  onHoverChange,
  disabled,
}: PersonaTemplateCardsProps) {
  const [hovered, setHovered] = useState<string | null>(null);

  const setHover = (id: string | null) => {
    setHovered(id);
    onHoverChange?.(id !== null);
  };

  return (
    <div>
      {/* Mobile: stacked rows */}
      <div className="sm:hidden flex flex-col gap-px bg-border">
        {templates.map((t, i) => (
          <button
            key={t.id}
            onClick={() => onSelect(t)}
            disabled={disabled}
            className="group relative overflow-hidden w-full text-left flex items-start gap-4 px-4 py-4 bg-card transition-colors duration-75 animate-fade-in disabled:opacity-30 before:absolute before:inset-0 before:-translate-x-full before:bg-foreground before:transition-transform before:duration-500 before:ease-[cubic-bezier(0.16,1,0.3,1)] active:before:translate-x-0"
            style={{ animationDelay: `${i * 80}ms`, animationFillMode: "backwards" }}
          >
            <span className="relative z-10 shrink-0 font-mono text-caption text-subtle-foreground group-active:text-background pt-px transition-colors">
              {i + 1} /
            </span>
            <div className="relative z-10 flex flex-col gap-1 min-w-0 flex-1">
              <span className="text-caption font-mono tracking-widest uppercase text-muted-foreground group-active:text-background transition-colors">
                {t.label}
              </span>
              <span className="text-detail font-mono text-subtle-foreground group-active:text-background/80 transition-colors leading-relaxed">
                {t.desc}
              </span>
            </div>
            <span className="relative z-10 shrink-0 font-mono text-detail text-subtle-foreground group-active:text-background pt-px transition-colors">
              ›
            </span>
          </button>
        ))}
      </div>

      {/* Desktop: 3-column cards with fill animation */}
      <div className="hidden sm:grid grid-cols-3 gap-px bg-border">
        {templates.map((t, i) => (
          <button
            key={t.id}
            onClick={() => onSelect(t)}
            onMouseEnter={() => setHover(t.id)}
            onMouseLeave={() => setHover(null)}
            disabled={disabled}
            className={cn(
              "group relative overflow-hidden text-left flex flex-col bg-card",
              "transition-[border-color] duration-300",
              "before:absolute before:inset-0 before:-translate-x-full before:bg-foreground",
              "before:transition-transform before:duration-500 before:ease-[cubic-bezier(0.16,1,0.3,1)]",
              "hover:before:translate-x-0",
              "animate-fade-in disabled:opacity-30",
            )}
            style={{ animationDelay: `${i * 80}ms`, animationFillMode: "backwards" }}
          >
            {/* Header */}
            <div className="relative z-10 flex items-start justify-between p-3 pb-2">
              <div className="flex flex-col gap-1">
                <span className="text-label font-mono text-subtle-foreground group-hover:text-background/50 transition-colors">
                  {String(i + 1).padStart(2, "0")} /
                </span>
                <span className="text-caption font-mono tracking-widest uppercase text-muted-foreground group-hover:text-background transition-colors">
                  {t.label}
                </span>
              </div>
              {/* Keyboard hint */}
              <span className="text-label font-mono text-subtle-foreground/50 group-hover:text-background/40 transition-colors border border-border group-hover:border-background/20 px-1.5 py-0.5">
                {i + 1}
              </span>
            </div>

            {/* Divider */}
            <div className="relative z-10 border-t border-border group-hover:border-foreground/10 mx-3 transition-colors" />

            {/* Description */}
            <div className="relative z-10 p-3 pt-2">
              <span className="text-detail font-mono text-subtle-foreground group-hover:text-background/70 transition-colors leading-snug">
                {t.desc}
              </span>
            </div>
          </button>
        ))}
      </div>

      {onBack && (
        <div className="flex justify-start mt-2">
          <Button size="xs" variant="ghost" onClick={onBack}>
            ← back
          </Button>
        </div>
      )}
    </div>
  );
}
