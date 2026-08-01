import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "../lib/isTauri";

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

type InvokeFn = <T>(cmd: string) => Promise<T>;

/**
 * Reads system stats from the Tauri host, or resolves null when running as a
 * plain web app where no native command exists.
 */
export async function fetchSystemStats(
  invokeFn: InvokeFn = invoke,
): Promise<SystemStats | null> {
  if (!isTauri()) return null;
  try {
    return await invokeFn<SystemStats>("get_system_stats");
  } catch {
    return null;
  }
}

export function useSystemStats(intervalMs = 1500) {
  const [stats, setStats] = useState<SystemStats | null>(null);

  useEffect(() => {
    // No native host in web mode — skip the interval entirely instead of
    // rejecting an invoke on every tick.
    if (!isTauri()) return;

    let cancelled = false;
    const poll = async () => {
      const data = await fetchSystemStats();
      if (!cancelled && data) setStats(data);
    };
    poll();
    const id = setInterval(poll, intervalMs);
    return () => { cancelled = true; clearInterval(id); };
  }, [intervalMs]);

  return stats;
}
