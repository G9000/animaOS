import type { NodeProps } from "@xyflow/react";
import { NodeShell, cn } from "@anima/standard-templates";
import { useSystemStats } from "../../../hooks/useSystemStats";
import type { SystemMonitorNode } from "./node-types";

function formatMb(mb: number) {
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
}

function barColor(pct: number) {
  if (pct > 85) return "bg-destructive/80";
  if (pct > 60) return "bg-yellow-400/70";
  return "bg-accent/80";
}

function valueColor(pct: number) {
  if (pct > 85) return "text-destructive";
  if (pct > 60) return "text-yellow-400/90";
  return "text-foreground/70";
}

// ── Icons ─────────────────────────────────────────────────────────────────────

function CpuIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <rect x="3.5" y="3.5" width="7" height="7" rx="1" stroke="currentColor" strokeWidth="1.2" />
      <line x1="5.5" y1="1"    x2="5.5" y2="3.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <line x1="8.5" y1="1"    x2="8.5" y2="3.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <line x1="5.5" y1="10.5" x2="5.5" y2="13"  stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <line x1="8.5" y1="10.5" x2="8.5" y2="13"  stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <line x1="1"   y1="5.5"  x2="3.5" y2="5.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <line x1="1"   y1="8.5"  x2="3.5" y2="8.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <line x1="10.5" y1="5.5" x2="13"  y2="5.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <line x1="10.5" y1="8.5" x2="13"  y2="8.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

function RamIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <rect x="1" y="4" width="12" height="6" rx="1" stroke="currentColor" strokeWidth="1.2" />
      <line x1="4"  y1="4" x2="4"  y2="10" stroke="currentColor" strokeWidth="1" strokeOpacity="0.5" />
      <line x1="7"  y1="4" x2="7"  y2="10" stroke="currentColor" strokeWidth="1" strokeOpacity="0.5" />
      <line x1="10" y1="4" x2="10" y2="10" stroke="currentColor" strokeWidth="1" strokeOpacity="0.5" />
      <line x1="2.5"  y1="2.5" x2="2.5"  y2="4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <line x1="5.5"  y1="2.5" x2="5.5"  y2="4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <line x1="8.5"  y1="2.5" x2="8.5"  y2="4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <line x1="11.5" y1="2.5" x2="11.5" y2="4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

function GpuUtilIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <path d="M2 10 A6 6 0 0 1 12 10" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <line x1="7" y1="10" x2="5" y2="5.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <circle cx="7" cy="10" r="0.8" fill="currentColor" />
    </svg>
  );
}

function AnimaIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <path d="M7 1.5L8.2 6.2L13 7L8.2 7.8L7 12.5L5.8 7.8L1 7L5.8 6.2L7 1.5Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
    </svg>
  );
}

// ── Row ───────────────────────────────────────────────────────────────────────

function StatRow({
  icon,
  label,
  value,
  hint,
  secondary,
  pct,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
  secondary?: string;
  pct: number | null;
}) {
  const color = pct !== null ? valueColor(pct) : "text-foreground/40";
  const hasSecondary = hint || secondary;

  return (
    <div className="flex items-start gap-3 px-4 py-3 border-b border-foreground/[0.04] last:border-0">
      <span className={cn("shrink-0 mt-[3px]", color)}>{icon}</span>

      <div className="flex-1 min-w-0 flex flex-col gap-2">
        {/* Primary row: label left, value right */}
        <div className="flex items-baseline justify-between gap-3">
          <span className="font-mono text-[8px] tracking-[0.25em] uppercase text-foreground/30 leading-none shrink-0">
            {label}
          </span>
          <span className={cn("font-mono text-[12px] leading-none tabular-nums", color)}>
            {value}
          </span>
        </div>

        {/* Secondary row: hint left, secondary right */}
        {hasSecondary && (
          <div className="flex items-baseline justify-between gap-3 -mt-0.5">
            <span className="font-mono text-[7px] text-foreground/20 leading-none truncate">
              {hint ?? ""}
            </span>
            {secondary && (
              <span className="font-mono text-[8px] text-foreground/30 leading-none tabular-nums">
                {secondary}
              </span>
            )}
          </div>
        )}

        {/* Bar */}
        <div className="h-[2px] w-full bg-foreground/[0.06] rounded-full overflow-hidden">
          {pct !== null ? (
            <div
              className={cn("h-full rounded-full transition-[width] duration-700", barColor(pct))}
              style={{ width: `${Math.min(pct, 100)}%` }}
            />
          ) : (
            <div className="h-full w-1/5 bg-foreground/[0.08] rounded-full" />
          )}
        </div>
      </div>
    </div>
  );
}

// ── Node ──────────────────────────────────────────────────────────────────────

export function SystemMonitorNode({ data }: NodeProps<SystemMonitorNode>) {
  const stats = useSystemStats();

  const ramPct = stats && stats.ram_total_mb > 0
    ? (stats.ram_used_mb / stats.ram_total_mb) * 100
    : null;

  const gpuName = stats?.gpu.name
    ?.replace(/^NVIDIA\s+/i, "")
    .replace(/^AMD\s+/i, "")
    .replace(/^Intel\s+/i, "")
    .trim() ?? null;

  return (
    <NodeShell title="System" onClose={data.onClose} className="w-64">
      <StatRow
        icon={<CpuIcon />}
        label="CPU"
        value={stats ? `${stats.cpu_usage.toFixed(0)}%` : "—"}
        hint={stats?.cpu_temp_c != null ? `${stats.cpu_temp_c.toFixed(0)}°C` : undefined}
        pct={stats?.cpu_usage ?? null}
      />
      <StatRow
        icon={<RamIcon />}
        label="RAM"
        value={
          stats
            ? `${formatMb(stats.ram_used_mb)} / ${formatMb(stats.ram_total_mb)}`
            : "—"
        }
        pct={ramPct}
      />
      <StatRow
        icon={<GpuUtilIcon />}
        label="GPU"
        value={stats?.gpu.usage != null ? `${stats.gpu.usage.toFixed(0)}%` : "—"}
        hint={[gpuName, stats?.gpu.temp_c != null ? `${stats.gpu.temp_c.toFixed(0)}°C` : null]
          .filter(Boolean).join(" · ") || undefined}
        secondary={
          stats?.gpu.vram_used_mb != null && stats?.gpu.vram_total_mb != null
            ? `${formatMb(stats.gpu.vram_used_mb)} / ${formatMb(stats.gpu.vram_total_mb)}`
            : undefined
        }
        pct={stats?.gpu.usage ?? null}
      />
      <StatRow
        icon={<AnimaIcon />}
        label="Anima"
        value={stats ? formatMb(stats.app_ram_mb) : "—"}
        pct={null}
      />
    </NodeShell>
  );
}
