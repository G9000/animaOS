import { createApiClient, type ApiClient } from "@anima/api-client";
import { API_BASE } from "./runtime";
import { getRuntimeNonce, refreshDaemonRuntimeNonce } from "./daemon";

const UNLOCK_TOKEN_KEY = "anima_unlock_token";
export const UNLOCK_SESSION_LOCKED_EVENT = "anima-unlock-session-locked";
let unlockTokenCache: string | null = null;

export function getUnlockToken(): string | null {
  if (unlockTokenCache) return unlockTokenCache;
  try {
    const stored = sessionStorage.getItem(UNLOCK_TOKEN_KEY);
    if (stored) unlockTokenCache = stored;
  } catch {
    // Ignore storage failures.
  }
  return unlockTokenCache;
}

export function setUnlockToken(token: string): void {
  unlockTokenCache = token;
  try {
    sessionStorage.setItem(UNLOCK_TOKEN_KEY, token);
  } catch {
    // Ignore storage failures.
  }
}

export function clearUnlockToken(): void {
  unlockTokenCache = null;
  try {
    sessionStorage.removeItem(UNLOCK_TOKEN_KEY);
    localStorage.removeItem(UNLOCK_TOKEN_KEY); // purge legacy
  } catch {
    // Ignore storage failures.
  }
}

function getRuntimeNonceSafely(): string | null {
  return getRuntimeNonce();
}

function extractRuntimeErrorMessage(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") {
    return typeof payload === "string" ? payload : null;
  }

  const candidate = payload as {
    error?: unknown;
    message?: unknown;
    detail?: unknown;
  };
  for (const value of [candidate.error, candidate.message, candidate.detail]) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return null;
}

async function readResponsePayload(response: Response): Promise<unknown> {
  try {
    return await response.clone().json();
  } catch {
    try {
      return await response.clone().text();
    } catch {
      return null;
    }
  }
}

function isInvalidSidecarNonceResponse(payload: unknown): boolean {
  const message = extractRuntimeErrorMessage(payload)?.toLowerCase() ?? "";
  return message.includes("sidecar nonce");
}

function isLockedSessionResponse(payload: unknown): boolean {
  const message = extractRuntimeErrorMessage(payload)?.toLowerCase() ?? "";
  return message.includes("session locked");
}

function emitUnlockSessionLocked(): void {
  try {
    globalThis.dispatchEvent(new CustomEvent(UNLOCK_SESSION_LOCKED_EVENT));
  } catch {
    // Ignore event dispatch failures outside browser-like runtimes.
  }
}

function setRuntimeNonceHeader(headers: Headers, nonce: string | null): void {
  if (nonce) {
    headers.set("x-anima-nonce", nonce);
  } else {
    headers.delete("x-anima-nonce");
  }
}

export async function fetchRuntimeWithNonceRefresh(
  input: RequestInfo | URL,
  init: RequestInit = {},
  allowRetry = true,
): Promise<Response> {
  const response = await fetch(input, init);
  if (response.status === 401) {
    const payload = await readResponsePayload(response);
    if (isLockedSessionResponse(payload)) {
      clearUnlockToken();
      emitUnlockSessionLocked();
    }
    return response;
  }

  if (!allowRetry || response.status !== 403) {
    return response;
  }

  const payload = await readResponsePayload(response);
  if (!isInvalidSidecarNonceResponse(payload)) {
    return response;
  }

  const refreshedNonce = await refreshDaemonRuntimeNonce().catch(() => null);
  if (!refreshedNonce) {
    return response;
  }

  const headers = new Headers(init.headers ?? {});
  setRuntimeNonceHeader(headers, refreshedNonce);
  return fetchRuntimeWithNonceRefresh(input, { ...init, headers }, false);
}

export function getRuntimeAuthHeaders(): HeadersInit {
  const headers: HeadersInit = {};

  const unlockToken = getUnlockToken();
  if (unlockToken) {
    headers["x-anima-unlock"] = unlockToken;
  }

  const runtimeNonce = getRuntimeNonce();
  if (runtimeNonce) {
    headers["x-anima-nonce"] = runtimeNonce;
  }

  return headers;
}

const baseApi = createApiClient({
  baseUrl: API_BASE,
  getUnlockToken,
  getNonce: getRuntimeNonceSafely,
  fetchImpl: fetchRuntimeWithNonceRefresh,
});

export const api: ApiClient & {
  translate: (text: string, targetLang: string) => Promise<string>;
} = {
  ...baseApi,
  translate: async (text: string, targetLang: string): Promise<string> => {
    const response = await fetch(
      `https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=${targetLang}&dt=t&q=${encodeURIComponent(text)}`,
    );
    const data = (await response.json()) as string[][][];
    return data[0].map((segment) => segment[0]).join("");
  },
};

