import { useState, useEffect, useCallback, useRef } from "react";
import type { ModConfigSchema, SetupStep } from "../components/mods/types";

const MOD_URL_KEY = "anima-mod-url";
const DEFAULT_MOD_URL = "http://localhost:3034";

export interface ModSummary {
  id: string;
  version: string;
  status: string;
  enabled: boolean;
  hasConfigSchema: boolean;
  hasSetupGuide: boolean;
}

export interface ModHealth {
  status: string;
  uptime: string | null;
  lastError: string | null;
}

export interface ModDetail {
  id: string;
  version: string;
  status: string;
  enabled: boolean;
  configSchema: ModConfigSchema | null;
  setupGuide: SetupStep[] | null;
  config: Record<string, unknown> | null;
  health: ModHealth | null;
}

export interface InstallModResult {
  id?: string;
  status?: string;
  [key: string]: unknown;
}

function buildModApiUrl(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/$/, "")}${path}`;
}

function extractErrorMessage(payload: unknown, status: number): string {
  if (payload && typeof payload === "object") {
    if ("message" in payload && typeof payload.message === "string" && payload.message.trim()) {
      return payload.message;
    }
    if ("error" in payload && typeof payload.error === "string" && payload.error.trim()) {
      return payload.error;
    }
  }
  if (typeof payload === "string" && payload.trim()) {
    return payload;
  }
  return `Request failed (${status})`;
}

async function requestJson<T>(
  baseUrl: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(buildModApiUrl(baseUrl, path), {
    ...init,
    headers,
  });

  const raw = await response.text();
  const payload = raw ? JSON.parse(raw) as unknown : null;

  if (!response.ok) {
    throw new Error(extractErrorMessage(payload, response.status));
  }

  return payload as T;
}

function createModClient(baseUrl: string) {
  return {
    listMods(): Promise<ModSummary[]> {
      return requestJson<ModSummary[]>(baseUrl, "/api/mods");
    },
    getModDetail(modId: string): Promise<ModDetail> {
      return requestJson<ModDetail>(baseUrl, `/api/mods/${modId}`);
    },
    enableMod(modId: string): Promise<{ status: string }> {
      return requestJson<{ status: string }>(baseUrl, `/api/mods/${modId}/enable`, {
        method: "POST",
      });
    },
    disableMod(modId: string): Promise<{ status: string }> {
      return requestJson<{ status: string }>(baseUrl, `/api/mods/${modId}/disable`, {
        method: "POST",
      });
    },
    restartMod(modId: string): Promise<{ status: string }> {
      return requestJson<{ status: string }>(baseUrl, `/api/mods/${modId}/restart`, {
        method: "POST",
      });
    },
    updateModConfig(
      modId: string,
      config: Record<string, unknown>,
    ): Promise<{ status: string }> {
      return requestJson<{ status: string }>(baseUrl, `/api/mods/${modId}/config`, {
        method: "PUT",
        body: JSON.stringify(config),
      });
    },
    installMod(source: string): Promise<InstallModResult> {
      return requestJson<InstallModResult>(baseUrl, "/api/mods/install", {
        method: "POST",
        body: JSON.stringify({ source }),
      });
    },
    getModHealth(modId: string): Promise<ModHealth> {
      return requestJson<ModHealth>(baseUrl, `/api/mods/${modId}/health`);
    },
  };
}

type ModClient = ReturnType<typeof createModClient>;

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
  const [mods, setMods] = useState<ModSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const client = getModClient();
      setMods(await client.listMods());
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
  const [mod, setMod] = useState<ModDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const client = getModClient();
      setMod(await client.getModDetail(modId));
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
