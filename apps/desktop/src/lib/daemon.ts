import type {
  DaemonCommand,
  DaemonControlRequest,
  DaemonControlResponse,
  DaemonErrorResponse,
  DaemonLogResponse,
  DaemonStatusResponse,
} from "@anima/daemon-contracts";

import {
  DAEMON_CONTROL_TOKEN_ENV,
  DAEMON_CONTROL_TOKEN_HEADER,
  DaemonRuntimeNonceResponse,
  DAEMON_ROUTES,
} from "@anima/daemon-contracts";

const DEFAULT_DAEMON_ORIGIN = "http://127.0.0.1:3032";
const DAEMON_CONTROL_TOKEN_KEY = "anima_daemon_control_token";
type DaemonHealthPayload = {
  controlToken?: string | null;
  control_token?: string | null;
};
let daemonRuntimeNonce: string | null = null;
let resolvingControlToken: Promise<string | null> | null = null;

function normalizeNonce(value: string | undefined | null): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function setRuntimeNonce(nonce: string | null) {
  daemonRuntimeNonce = normalizeNonce(nonce);
}

export function getRuntimeNonce(): string | null {
  return daemonRuntimeNonce;
}

function getDaemonOrigin(): string {
  return (
    (import.meta.env?.VITE_DAEMON_ORIGIN as string | undefined) ??
    DEFAULT_DAEMON_ORIGIN
  ).replace(/\/$/, "");
}

function getControlToken(): string | null {
  try {
    return (
      localStorage.getItem(DAEMON_CONTROL_TOKEN_KEY)
      ?? localStorage.getItem(DAEMON_CONTROL_TOKEN_ENV)
    );
  } catch {
    return null;
  }
}

function clearStoredControlToken(): void {
  try {
    localStorage.removeItem(DAEMON_CONTROL_TOKEN_KEY);
  } catch {
    // Ignore storage failures.
  }
}

function getHeaders() {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const token = getControlToken();
  if (token) {
    headers[DAEMON_CONTROL_TOKEN_HEADER] = token;
  }
  return headers;
}

function endpoint(path: string): string {
  const origin = getDaemonOrigin();
  if (path.startsWith("/")) {
    return `${origin}/${path.replace(/^\//, "")}`;
  }
  return `${origin}/${path}`;
}

function parseErrorResponse(payload: unknown): string {
  if (typeof payload === "string") return payload;
  if (payload && typeof payload === "object") {
    const response = payload as DaemonErrorResponse;
    if (response.message) {
      return response.message;
    }
    if (response.detail) {
      return response.detail;
    }
  }
  return "Daemon control request failed";
}

function parseControlToken(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const candidate = payload as DaemonHealthPayload;
  const raw = candidate.controlToken ?? candidate.control_token;
  if (!raw) {
    return null;
  }
  const trimmed = raw.trim();
  return trimmed.length > 0 ? trimmed : null;
}

async function bootstrapControlToken(): Promise<string | null> {
  if (resolvingControlToken) {
    return resolvingControlToken;
  }

  resolvingControlToken = (async () => {
    const response = await fetch(endpoint(DAEMON_ROUTES.health), {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      return null;
    }

    const text = await response.text();
    let payload: unknown = text;
    try {
      payload = text ? JSON.parse(text) : null;
    } catch {
      payload = null;
    }

    const token = parseControlToken(payload);
    if (token) {
      setDaemonControlToken(token);
    }
    return token;
  })();

  try {
    return await resolvingControlToken;
  } finally {
    resolvingControlToken = null;
  }
}

async function request<T>(path: string, init: RequestInit = {}, allowRetry = true): Promise<T> {
  const response = await fetch(endpoint(path), {
    ...init,
    headers: {
      ...getHeaders(),
      ...(init.headers ?? {}),
    },
  });

  const text = await response.text();
  let parsed: unknown = text;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = text;
  }

  if (!response.ok) {
    if (response.status === 401 || response.status === 403) {
      clearStoredControlToken();
      if (allowRetry) {
        const token = await bootstrapControlToken();
        if (token) {
          return request(path, init, false);
        }
      }
    }
    const message = parseErrorResponse(parsed);
    throw new Error(message);
  }

  return parsed as T;
}

export async function getDaemonHealth(): Promise<{ status: string; version: string; updatedAt: string }> {
  return request(`${DAEMON_ROUTES.health}`);
}

export async function getDaemonStatus(): Promise<DaemonStatusResponse> {
  return request<DaemonStatusResponse>(`${DAEMON_ROUTES.status}`);
}

async function refreshRuntimeNonceAfterControl(): Promise<void> {
  try {
    await refreshDaemonRuntimeNonce();
  } catch {
    // Ignore failures so control actions remain idempotent on older runtimes.
  }
}

export async function refreshDaemonRuntimeNonce(): Promise<string | null> {
  const nonce = await getDaemonRuntimeNonce();
  if (!nonce) {
    return null;
  }
  setRuntimeNonce(nonce);
  return nonce;
}

async function getDaemonRuntimeNonce(): Promise<string | null> {
  const payload = await request<DaemonRuntimeNonceResponse>(DAEMON_ROUTES.nonce);
  return normalizeNonce(payload.runtimeNonce);
}

export async function getDaemonLogs(lines = 120): Promise<DaemonLogResponse> {
  const url = new URL(`${DAEMON_ROUTES.logs}`, getDaemonOrigin());
  url.searchParams.set("lines", String(lines));
  return request(url.pathname + url.search);
}

export async function controlDaemon(command: DaemonCommand, requestBody?: DaemonControlRequest): Promise<DaemonControlResponse> {
  const response = await request<DaemonControlResponse>(`${DAEMON_ROUTES.control}/${command}`, {
    method: "POST",
    body: requestBody ? JSON.stringify(requestBody) : undefined,
  });
  return response;
}

export async function startDaemon(): Promise<void> {
  await controlDaemon("start");
  await refreshRuntimeNonceAfterControl();
}

export async function stopDaemon(): Promise<void> {
  await controlDaemon("stop");
  setRuntimeNonce(null);
}

export async function restartDaemon(): Promise<void> {
  await controlDaemon("restart");
  await refreshRuntimeNonceAfterControl();
}

export async function setDaemonLock(locked: boolean): Promise<void> {
  await controlDaemon(locked ? "lock" : "unlock");
}

export async function setDaemonBackground(enabled: boolean): Promise<void> {
  await controlDaemon("set-background", { backgroundEnabled: enabled });
}

export function setDaemonControlToken(token: string): void {
  try {
    localStorage.setItem(DAEMON_CONTROL_TOKEN_KEY, token);
  } catch {
    // Ignore storage errors for best-effort token persistence.
  }
}
