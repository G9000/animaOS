import { useEffect, useRef, useState } from "react";
import { cn, liftUpRight, InboxIcon, TargetIcon, SparkleIcon, TrendIcon, type IconProps } from "@anima/standard-templates";
import type { ComponentType } from "react";
import { useLayoutActions } from "../../context/LayoutActionsContext";
import { CLIP_PATH } from "./hud.styles";

type InboxKey = "messages" | "tasks" | "nudges" | "activity";

const INBOX_ITEMS: { key: InboxKey; Icon: ComponentType<IconProps> }[] = [
  { key: "messages", Icon: InboxIcon   },
  { key: "tasks",    Icon: TargetIcon  },
  { key: "nudges",   Icon: SparkleIcon },
  { key: "activity", Icon: TrendIcon   },
];

const PLACEHOLDERS: Record<InboxKey, string[]> = {
  messages: ["Hey, how are you feeling today?", "Your reflection is ready"],
  tasks:    ["Review weekly goals", "Journal entry pending", "Check in with Anima"],
  nudges:   [],
  activity: ["Memory consolidated", "Goal streak: 4 days", "New insight added"],
};

export function InboxPanel() {
  const { nudgeCount } = useLayoutActions();
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<InboxKey | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const counts: Record<InboxKey, number> = {
    messages: 2,
    tasks:    3,
    nudges:   nudgeCount,
    activity: 0,
  };

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setCategory(null);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <div ref={ref} className="fixed bottom-0 left-0 z-30 w-42 pointer-events-auto">
      {open && category && (
        <div
          style={{ clipPath: CLIP_PATH.cutTopRight }}
          className="absolute bottom-full left-0 w-64 bg-background/95 backdrop-blur-[44px] border border-hairline shadow-[0_8px_32px_rgba(0,0,0,0.20)] flex flex-col"
        >
          <div className="px-4 py-2 border-b border-hairline-faint">
            <span className="font-mono text-micro uppercase tracking-caps-4 text-foreground/30">
              {category}
            </span>
          </div>
          {(() => {
            const items =
              category === "nudges"
                ? Array.from({ length: nudgeCount }, (_, i) => `Nudge ${i + 1}`)
                : PLACEHOLDERS[category];
            return items.length > 0 ? (
              items.map((item, i) => (
                <button
                  key={i}
                  className="w-full text-left px-4 py-2 font-mono text-caption text-foreground/40 hover:text-foreground hover:bg-foreground/[0.04] transition-colors truncate"
                >
                  {item}
                </button>
              ))
            ) : (
              <p className="px-4 py-3 font-mono text-label text-foreground/20 uppercase">None</p>
            );
          })()}
        </div>
      )}

      <div
        style={{ clipPath: CLIP_PATH.cutTopRight }}
        className="bg-accent flex items-stretch divide-x divide-accent-foreground/20"
      >
        {INBOX_ITEMS.map(({ key, Icon }) => {
          const count = counts[key];
          const isActive = open && category === key;
          return (
            <button
              key={key}
              onClick={() => {
                if (isActive) { setOpen(false); setCategory(null); }
                else { setOpen(true); setCategory(key); }
              }}
              aria-pressed={isActive}
              className={cn(
                liftUpRight,
                "flex flex-1 justify-center items-center gap-1.5 px-2 py-2 text-accent-foreground transition-colors duration-150",
                isActive ? "bg-black/20" : "hover:bg-black/10",
              )}
            >
              <Icon size="sm" />
              {count > 0 && (
                <span className="font-mono text-label leading-none opacity-70">{count}</span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
