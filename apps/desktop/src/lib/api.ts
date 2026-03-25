import { createApiClient, type ApiClient } from "@anima/api-client";
import { API_BASE } from "./runtime";

const UNLOCK_TOKEN_KEY = "anima_unlock_token";
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

const baseApi = createApiClient({
  baseUrl: API_BASE,
  getUnlockToken,
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

