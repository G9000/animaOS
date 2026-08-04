import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "../lib/isTauri";

export interface NetworkStats {
  download_kbps: number;
  upload_kbps: number;
}

export interface NetworkStatsWithPeak extends NetworkStats {
  peak_kbps: number;
}

type InvokeFn = <T>(cmd: string) => Promise<T>;

/**
 * Reads network stats from the Tauri host, or resolves null when running as a
 * plain web app where no native command exists.
 */
export async function fetchNetworkStats(
  invokeFn: InvokeFn = invoke,
): Promise<NetworkStats | null> {
  if (!isTauri()) return null;
  try {
    return await invokeFn<NetworkStats>("get_network_stats");
  } catch {
    return null;
  }
}

export function useNetworkStats(intervalMs = 1500) {
  const [stats, setStats] = useState<NetworkStatsWithPeak | null>(null);
  const peakRef = useRef(1024); // floor at 1 MB/s so the bar isn't always full at idle

  useEffect(() => {
    // No native host in web mode — skip the interval entirely instead of
    // rejecting an invoke on every tick.
    if (!isTauri()) return;

    let cancelled = false;
    const poll = async () => {
      const data = await fetchNetworkStats();
      if (!cancelled && data) {
        const max = Math.max(data.download_kbps, data.upload_kbps);
        if (max > peakRef.current) peakRef.current = max;
        setStats({ ...data, peak_kbps: peakRef.current });
      }
    };
    poll();
    const id = setInterval(poll, intervalMs);
    return () => { cancelled = true; clearInterval(id); };
  }, [intervalMs]);

  return stats;
}
