import {
  createApiClient,
  type AgentConfig,
  type AgentResponse,
  type ApiClient,
  type AuthResponse,
  type ChatMessage,
  type DailyBrief,
  type EmotionalContextData,
  type Greeting,
  type HomeData,
  type LoginResponse,
  type MemoryEpisodeData,
  type MemoryItemData,
  type MemoryOverviewData,
  type MemorySearchResult,
  type Nudge,
  type PersonaTemplate,
  type ProviderInfo,
  type SelfModelData,
  type SelfModelSection,
  type TaskItem,
  type User,
  type DbTableInfo,
  type DbTableData,
  type DbQueryResult,
} from "@anima/api-client";
import { API_BASE } from "./runtime";

const UNLOCK_TOKEN_KEY = "anima_unlock_token";
let unlockTokenCache: string | null = null;

export function getUnlockToken(): string | null {
  if (unlockTokenCache) return unlockTokenCache;
  try {
    const stored = localStorage.getItem(UNLOCK_TOKEN_KEY);
    if (stored) unlockTokenCache = stored;
    return unlockTokenCache;
  } catch {
    return null;
  }
}

export function setUnlockToken(token: string): void {
  unlockTokenCache = token;
  try {
    localStorage.setItem(UNLOCK_TOKEN_KEY, token);
  } catch {
    // Ignore storage failures.
  }
}

export function clearUnlockToken(): void {
  unlockTokenCache = null;
  try {
    localStorage.removeItem(UNLOCK_TOKEN_KEY);
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

export type {
  DbTableInfo,
  DbTableData,
  DbQueryResult,
  AgentConfig,
  AgentResponse,
  AuthResponse,
  ChatMessage,
  DailyBrief,
  EmotionalContextData,
  Greeting,
  HomeData,
  LoginResponse,
  MemoryEpisodeData,
  MemoryItemData,
  MemoryOverviewData,
  MemorySearchResult,
  Nudge,
  PersonaTemplate,
  ProviderInfo,
  SelfModelData,
  SelfModelSection,
  TaskItem,
  User,
};
