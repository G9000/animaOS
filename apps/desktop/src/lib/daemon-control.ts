import {
  DAEMON_CONTROL_PREFIX,
  DAEMON_ROUTES,
  type DaemonClientIdentity,
  type DaemonCommandResponse,
  type DaemonHealthResponse,
  type DaemonLockRequest,
  type DaemonOpenLogsResponse,
  type DaemonOpenLogsRequest,
  type DaemonRestartRequest,
  type DaemonStartRequest,
  type DaemonStopRequest,
  type DaemonUnlockRequest,
} from "@anima/daemon-contracts";

const defaultDaemonBase = import.meta.env.VITE_DAEMON_CONTROL_BASE_URL ?? "http://127.0.0.1:4044";

function trimBaseUrl(base: string): string {
  return base.replace(/\/+$/, "");
}

function getDaemonBaseUrl(): string {
  return trimBaseUrl(defaultDaemonBase);
}

function getDaemonToken(): string | null {
  return (
    (import.meta.env.VITE_DAEMON_CONTROL_TOKEN as string | undefined)?.trim() ??
    null
  );
}

async function parseJsonOrThrow<T>(response: Response): Promise<T> {
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      typeof payload === "object" && payload && "message" in payload
        ? String((payload as { message?: unknown }).message ?? "Daemon request failed")
        : "Daemon request failed";
    throw new Error(message);
  }
  return payload as T;
}

function makeHeaders(): HeadersInit {
  const headers: HeadersInit = { "Content-Type": "application/json" };
  const token = getDaemonToken();
  if (token) {
    headers["x-anima-daemon-token"] = token;
  }
  return headers;
}

function buildUrl(path: string): string {
  return `${getDaemonBaseUrl()}${path.startsWith("/") ? path : `/${path}`}`;
}

export async function daemonStatus(): Promise<DaemonHealthResponse> {
  const response = await fetch(`${buildUrl(DAEMON_ROUTES.STATUS)}`, {
    method: "GET",
    headers: makeHeaders(),
  });
  return parseJsonOrThrow<DaemonHealthResponse>(response);
}

export async function daemonStart(
  params: DaemonStartRequest,
): Promise<DaemonCommandResponse> {
  const response = await fetch(buildUrl(DAEMON_ROUTES.START), {
    method: "POST",
    headers: makeHeaders(),
    body: JSON.stringify(params),
  });
  return parseJsonOrThrow<DaemonCommandResponse>(response);
}

export async function daemonStop(
  params: DaemonStopRequest,
): Promise<DaemonCommandResponse> {
  const response = await fetch(buildUrl(DAEMON_ROUTES.STOP), {
    method: "POST",
    headers: makeHeaders(),
    body: JSON.stringify(params),
  });
  return parseJsonOrThrow<DaemonCommandResponse>(response);
}

export async function daemonRestart(
  params: DaemonRestartRequest,
): Promise<DaemonCommandResponse> {
  const response = await fetch(buildUrl(DAEMON_ROUTES.RESTART), {
    method: "POST",
    headers: makeHeaders(),
    body: JSON.stringify(params),
  });
  return parseJsonOrThrow<DaemonCommandResponse>(response);
}

export async function daemonLock(
  params: DaemonLockRequest,
): Promise<DaemonCommandResponse> {
  const response = await fetch(buildUrl(DAEMON_ROUTES.LOCK), {
    method: "POST",
    headers: makeHeaders(),
    body: JSON.stringify(params),
  });
  return parseJsonOrThrow<DaemonCommandResponse>(response);
}

export async function daemonUnlock(
  params: DaemonUnlockRequest,
): Promise<DaemonCommandResponse> {
  const response = await fetch(buildUrl(DAEMON_ROUTES.UNLOCK), {
    method: "POST",
    headers: makeHeaders(),
    body: JSON.stringify(params),
  });
  return parseJsonOrThrow<DaemonCommandResponse>(response);
}

export async function daemonOpenLogs(
  params: DaemonOpenLogsRequest,
): Promise<DaemonOpenLogsResponse> {
  const response = await fetch(buildUrl(DAEMON_ROUTES.OPEN_LOGS), {
    method: "POST",
    headers: makeHeaders(),
    body: JSON.stringify(params),
  });
  return parseJsonOrThrow<DaemonOpenLogsResponse>(response);
}

export async function reportDaemonClientIdentity(identity: DaemonClientIdentity): Promise<void> {
  await fetch(buildUrl(`${DAEMON_CONTROL_PREFIX}/clients/${identity.clientId}/heartbeat`), {
    method: "POST",
    headers: makeHeaders(),
    body: JSON.stringify(identity),
  }).then((response) => {
    if (!response.ok) {
      void response.text();
    }
  });
}
