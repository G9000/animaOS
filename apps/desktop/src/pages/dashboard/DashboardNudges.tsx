import type { Nudge } from "../../lib/api";

interface DashboardNudgesProps {
  nudges: Nudge[];
  onDismiss: (type: string) => void;
}

export function DashboardNudges({ nudges, onDismiss }: DashboardNudgesProps) {
  if (!nudges.length) return null;

  return (
    <div className="space-y-2">
      {nudges.map((nudge) => (
        <div
          key={nudge.type}
          className="flex items-center justify-between gap-3 px-4 py-2.5 bg-bg-card/50 border border-border rounded-lg"
        >
          <span className="text-xs text-text-muted">{nudge.message}</span>
          <button
            onClick={() => onDismiss(nudge.type)}
            className="text-[10px] text-text-muted/40 hover:text-text-muted shrink-0"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
