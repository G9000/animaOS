import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

export interface GpuInfo {
  name: string | null;
  usage: number | null;
  temp_c: number | null;
  vram_used_mb: number | null;
  vram_total_mb: number | null;
}

export interface SystemStats {
  cpu_usage: number;
  cpu_temp_c: number | null;
  ram_used_mb: number;
  ram_total_mb: number;
  app_ram_mb: number;
  gpu: GpuInfo;
}

export function useSystemStats(intervalMs = 1500) {
  const [stats, setStats] = useState<SystemStats | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await invoke<SystemStats>("get_system_stats");
        if (!cancelled) setStats(data);
      } catch {}
    };
    poll();
    const id = setInterval(poll, intervalMs);
    return () => { cancelled = true; clearInterval(id); };
  }, [intervalMs]);

  return stats;
}
