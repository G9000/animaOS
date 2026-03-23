import { useNavigate } from "react-router-dom";
import StatusBadge from "./StatusBadge";

interface ModCardProps {
  id: string;
  version: string;
  status: string;
  enabled: boolean;
  hasConfigSchema: boolean;
  onToggle: (id: string, enable: boolean) => void;
}

export default function ModCard({ id, version, status, enabled, hasConfigSchema, onToggle }: ModCardProps) {
  const navigate = useNavigate();

  return (
    <div
      onClick={() => navigate(`/mods/${id}`)}
      className={`group cursor-pointer border border-border p-4 transition-all hover:border-text-muted/30 ${
        !enabled ? "opacity-40" : ""
      }`}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="font-mono text-[10px] tracking-widest uppercase text-text">
          {id}
        </span>
        <StatusBadge status={status} />
      </div>

      <div className="flex items-center justify-between mt-3">
        <span className="font-mono text-[8px] text-text-muted/40">v{version}</span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onToggle(id, !enabled);
          }}
          className={`w-7 h-4 rounded-full transition-colors relative ${
            enabled ? "bg-primary/30" : "bg-bg-input"
          }`}
        >
          <div
            className={`absolute top-0.5 w-3 h-3 rounded-full transition-all ${
              enabled ? "left-3.5 bg-primary" : "left-0.5 bg-text-muted/30"
            }`}
          />
        </button>
      </div>
    </div>
  );
}
