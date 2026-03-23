import { createModClient, type ModClient } from "@anima/mod-client";
import { useState, useEffect, useCallback, useRef } from "react";

const MOD_URL_KEY = "anima-mod-url";
const DEFAULT_MOD_URL = "http://localhost:3034";

export function getModUrl(): string {
  try {
    return localStorage.getItem(MOD_URL_KEY) || DEFAULT_MOD_URL;
  } catch {
    return DEFAULT_MOD_URL;
  }
}

export function setModUrl(url: string): void {
  localStorage.setItem(MOD_URL_KEY, url);
}

let clientInstance: ModClient | null = null;

export function getModClient(): ModClient {
  if (!clientInstance) {
    clientInstance = createModClient(getModUrl());
  }
  return clientInstance;
}

/** Reset client (call after URL change) */
export function resetModClient(): void {
  clientInstance = null;
}

/** Hook: fetch all mods */
export function useMods() {
  const [mods, setMods] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const client = getModClient();
      const { data, error: err } = await client.api.mods.get();
      if (err) throw new Error(String(err));
      setMods(data ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to connect to anima-mod");
      setMods([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  return { mods, loading, error, refresh };
}

/** Hook: fetch single mod detail */
export function useModDetail(modId: string) {
  const [mod, setMod] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const client = getModClient();
      const { data, error: err } = await client.api.mods({ id: modId }).get();
      if (err) throw new Error(String(err));
      setMod(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch mod");
    } finally {
      setLoading(false);
    }
  }, [modId]);

  useEffect(() => { refresh(); }, [refresh]);

  return { mod, loading, error, refresh };
}

/** Hook: WebSocket events from anima-mod */
export function useModEvents(onEvent: (event: any) => void) {
  const wsRef = useRef<WebSocket | null>(null);
  const callbackRef = useRef(onEvent);
  callbackRef.current = onEvent;

  useEffect(() => {
    const url = getModUrl().replace(/^http/, "ws") + "/api/events";
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data);
        callbackRef.current(event);
      } catch { /* ignore parse errors */ }
    };

    ws.onerror = () => { /* silent — caller can refresh on reconnect */ };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, []);
}
