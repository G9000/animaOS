import { useState } from "react";
import { getDbViewerEnabled, setDbViewerEnabled, getShowTrace, setShowTrace } from "../../lib/preferences";
import { glass } from "@anima/standard-templates";


export default function AdvancedSettings() {
  const [dbViewer, setDbViewer] = useState(getDbViewerEnabled);
  const [showTrace, setShowTraceState] = useState(getShowTrace);

  return (
    <div className={`${glass} p-6 space-y-5`}>
      <h2 className="font-mono text-label tracking-caps-4 uppercase text-foreground/40">
        Debug Tools
      </h2>

      <div className="h-px bg-foreground/[0.06]" />

      <ToggleRow
        label="Agent Trace Panel"
        description="Real-time agent execution trace in chat. Reveals tool calls, reasoning steps, and timing. Toggle with Ctrl+Shift+T."
        checked={showTrace}
        onChange={(enabled) => { setShowTraceState(enabled); setShowTrace(enabled); }}
      />

      <div className="h-px bg-foreground/[0.06]" />

      <ToggleRow
        label="Database Viewer"
        description="DB inspector in the sidebar. Browse tables, run queries, edit or delete rows."
        checked={dbViewer}
        onChange={(enabled) => { setDbViewer(enabled); setDbViewerEnabled(enabled); }}
      />
    </div>
  );
}

function ToggleRow({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-start justify-between gap-6 cursor-pointer group">
      <div className="space-y-1 min-w-0">
        <p className="font-mono text-detail tracking-wide text-foreground/70 group-hover:text-foreground transition-colors">
          {label}
        </p>
        <p className="font-mono text-caption text-foreground/30 leading-relaxed tracking-wide">
          {description}
        </p>
      </div>
      <button
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={[
          "relative mt-0.5 inline-flex h-5 w-9 shrink-0 cursor-pointer border transition-all duration-200",
          checked ? "border-accent/50 bg-accent/[0.12]" : "border-hairline bg-foreground/[0.04]",
        ].join(" ")}
      >
        <span
          className={[
            "pointer-events-none absolute top-0.5 h-3.5 w-3.5 transition-all duration-200",
            checked ? "left-[18px] bg-accent" : "left-0.5 bg-foreground/20",
          ].join(" ")}
        />
      </button>
    </label>
  );
}
