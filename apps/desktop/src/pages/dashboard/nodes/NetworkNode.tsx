import type { NodeProps } from "@xyflow/react";
import { NodeShell, cn } from "@anima/standard-templates";
import { useNetworkStats } from "../../../hooks/useNetworkStats";
import type { NetworkNode } from "./node-types";

function formatSpeed(kbps: number) {
  if (kbps >= 1024 * 1024) return `${(kbps / 1024 / 1024).toFixed(2)} GB/s`;
  if (kbps >= 1024) return `${(kbps / 1024).toFixed(1)} MB/s`;
  return `${kbps.toFixed(0)} KB/s`;
}

function NetworkIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <circle cx="7" cy="7" r="5.5" stroke="currentColor" strokeWidth="1.2" />
      <path d="M7 1.5 C5 3.5 5 10.5 7 12.5 C9 10.5 9 3.5 7 1.5Z" stroke="currentColor" strokeWidth="1.2" />
      <line x1="1.5" y1="7" x2="12.5" y2="7" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

export function NetworkNode({ data }: NodeProps<NetworkNode>) {
  const stats = useNetworkStats();

  const dl = stats?.download_kbps ?? 0;
  const ul = stats?.upload_kbps ?? 0;
  const peak = stats?.peak_kbps ?? 1024;
  const total = dl + ul;
  const pct = peak > 0 ? Math.min((total / (peak * 2)) * 100, 100) : 0;
  const active = total > 10;

  return (
    <NodeShell title="Network" onClose={data.onClose} className="w-64">
      <div className="flex items-start gap-3 px-4 py-3">
        <span className="shrink-0 mt-[3px] text-foreground/30">
          <NetworkIcon />
        </span>

        <div className="flex-1 flex flex-col gap-2">
          {/* Speed pair */}
          <div className="flex items-baseline justify-between gap-3">
            <span className="font-mono text-[8px] tracking-[0.25em] uppercase text-foreground/30 leading-none">
              Speed
            </span>
            <div className="flex items-baseline gap-2">
              <span className={cn("font-mono text-[9px] leading-none tabular-nums", active ? "text-foreground/30" : "text-foreground/15")}>
                ↓ {formatSpeed(dl)}
              </span>
              <span className={cn("font-mono text-[9px] leading-none tabular-nums", active ? "text-foreground/30" : "text-foreground/15")}>
                ↑ {formatSpeed(ul)}
              </span>
            </div>
          </div>

          {/* Total throughput as primary value */}
          <div className="flex items-baseline justify-between">
            <span className="font-mono text-[7px] text-foreground/20 leading-none">total</span>
            <span className={cn(
              "font-mono text-[12px] leading-none tabular-nums",
              active ? "text-foreground/70" : "text-foreground/25",
            )}>
              {formatSpeed(total)}
            </span>
          </div>

          {/* Bar */}
          <div className="h-[2px] w-full bg-foreground/[0.06] rounded-full overflow-hidden">
            <div
              className={cn(
                "h-full rounded-full transition-[width] duration-700",
                active ? "bg-accent/80" : "bg-foreground/[0.08]",
              )}
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      </div>
    </NodeShell>
  );
}
