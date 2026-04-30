import { useNavigate } from "react-router-dom";
import StatusBadge from "./StatusBadge";

interface ModCardProps {
  id: string;
  version: string;
  status: string;
  enabled: boolean;
  hasConfigSchema?: boolean;
  hasSetupGuide?: boolean;
  toolsCount?: number;
  canUninstall?: boolean;
  busy?: boolean;
  error?: string | null;
  onToggle: (id: string, enable: boolean) => void | Promise<void>;
}

export default function ModCard({
  id,
  version,
  status,
  enabled,
  hasConfigSchema,
  hasSetupGuide,
  toolsCount = 0,
  canUninstall,
  busy,
  error,
  onToggle,
}: ModCardProps) {
  const navigate = useNavigate();

  return (
    <div
      onClick={() => navigate(`/mods/${id}`)}
      className={`group cursor-pointer border border-border p-4 transition-all hover:border-text-muted/30 ${
        !enabled ? "opacity-40" : ""
      }`}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="font-mono text-[10px] tracking-widest uppercase text-foreground">
          {id}
        </span>
        <StatusBadge status={status} />
      </div>

      <div className="flex flex-wrap gap-1 min-h-[18px]">
        {hasConfigSchema && (
          <span className="font-mono text-[7px] tracking-widest text-muted-foreground/40 border border-border px-1.5 py-0.5">
            CFG
          </span>
        )}
        {hasSetupGuide && (
          <span className="font-mono text-[7px] tracking-widest text-muted-foreground/40 border border-border px-1.5 py-0.5">
            SETUP
          </span>
        )}
        {toolsCount > 0 && (
          <span className="font-mono text-[7px] tracking-widest text-muted-foreground/40 border border-border px-1.5 py-0.5">
            TOOLS {toolsCount}
          </span>
        )}
        {canUninstall && (
          <span className="font-mono text-[7px] tracking-widest text-muted-foreground/40 border border-border px-1.5 py-0.5">
            USER
          </span>
        )}
      </div>

      <div className="flex items-center justify-between mt-3">
        <span className="font-mono text-[8px] text-muted-foreground/40">v{version}</span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onToggle(id, !enabled);
          }}
          disabled={busy}
          className={`w-7 h-4 rounded-full transition-colors relative ${
            enabled ? "bg-primary/30" : "bg-input"
          } disabled:opacity-40`}
          title={enabled ? "Disable mod" : "Enable mod"}
        >
          <div
            className={`absolute top-0.5 w-3 h-3 rounded-full transition-all ${
              enabled ? "left-3.5 bg-primary" : "left-0.5 bg-text-muted/30"
            }`}
          />
        </button>
      </div>
      {error && (
        <div className="font-mono text-[8px] text-destructive mt-2 truncate" title={error}>
          {error}
        </div>
      )}
    </div>
  );
}
