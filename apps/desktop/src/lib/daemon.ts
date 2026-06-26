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
  DAEMON_ROUTES,
} from "@anima/daemon-contracts";

const DEFAULT_DAEMON_ORIGIN = "http://127.0.0.1:3032";
const DAEMON_CONTROL_TOKEN_KEY = "anima_daemon_control_token";

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

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
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
    const message = parseErrorResponse(parsed);
    throw new Error(message);
  }

  return parsed as T;
}

export async function getDaemonHealth(): Promise<{ status: string; version: string; updatedAt: string }> {
  return request(`${DAEMON_ROUTES.health}`);
}

export async function getDaemonStatus(): Promise<DaemonStatusResponse> {
  return request(`${DAEMON_ROUTES.status}`);
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
}

export async function stopDaemon(): Promise<void> {
  await controlDaemon("stop");
}

export async function restartDaemon(): Promise<void> {
  await controlDaemon("restart");
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
