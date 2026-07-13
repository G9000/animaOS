import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

export interface NetworkStats {
  download_kbps: number;
  upload_kbps: number;
}

export interface NetworkStatsWithPeak extends NetworkStats {
  peak_kbps: number;
}

export function useNetworkStats(intervalMs = 1500) {
  const [stats, setStats] = useState<NetworkStatsWithPeak | null>(null);
  const peakRef = useRef(1024); // floor at 1 MB/s so the bar isn't always full at idle

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await invoke<NetworkStats>("get_network_stats");
        if (!cancelled) {
          const max = Math.max(data.download_kbps, data.upload_kbps);
          if (max > peakRef.current) peakRef.current = max;
          setStats({ ...data, peak_kbps: peakRef.current });
        }
      } catch {}
    };
    poll();
    const id = setInterval(poll, intervalMs);
    return () => { cancelled = true; clearInterval(id); };
  }, [intervalMs]);

  return stats;
}
