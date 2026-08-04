import { cn, Button, glass } from "@anima/standard-templates";

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
  return (
    <div className="flex flex-col items-center gap-3">

      <div className="grid grid-cols-2 gap-3">
        {templates.map((t, i) => (
          <button
            key={t.id}
            onClick={() => onSelect(t)}
            onMouseEnter={() => onHoverChange?.(true)}
            onMouseLeave={() => onHoverChange?.(false)}
            disabled={disabled}
            className={cn(
              glass,
              "group relative overflow-hidden text-left flex flex-col w-56 animate-fade-in disabled:opacity-30",
              "before:absolute before:inset-0 before:-translate-y-full before:bg-accent/20",
              "before:transition-transform before:duration-400 before:ease-[cubic-bezier(0.16,1,0.3,1)]",
              "hover:before:translate-y-0 hover:border-accent/60 transition-[border-color] duration-200",
            )}
            style={{ animationDelay: `${i * 80}ms`, animationFillMode: "backwards" }}
          >
            {/* Accent label */}
            <div className="relative z-10 bg-accent/0 group-hover:bg-accent/20 transition-colors px-3 py-1.5 border-b border-hairline group-hover:border-accent/30 flex items-center justify-between">
              <span className="font-mono text-ui font-semibold tracking-caps-4 text-foreground uppercase">
                {t.label}
              </span>
              <span className="font-mono text-detail text-muted-foreground/40 group-hover:text-accent/60 border border-hairline group-hover:border-accent/40 px-1.5 py-0.5 transition-colors tabular-nums">
                {i + 1}
              </span>
            </div>

            {/* Description */}
            <div className="relative z-10 px-3 py-3 flex-1">
              <p className="font-mono text-detail text-muted-foreground/60 group-hover:text-foreground/80 leading-relaxed transition-colors">
                {t.desc}
              </p>
            </div>
          </button>
        ))}
      </div>

      {onBack && (
        <div className={glass}>
          <Button size="xs" variant="main" onClick={onBack}>← back</Button>
        </div>
      )}

    </div>
  );
}
