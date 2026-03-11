import {
  createApiClient,
  type AgentConfig,
  type AgentResponse,
  type ApiClient,
  type AuthResponse,
  type ChatMessage,
  type DailyBrief,
  type HomeData,
  type LoginResponse,
  type MemoryEntry,
  type MemoryFile,
  type Nudge,
  type PersonaTemplate,
  type ProviderInfo,
  type TaskItem,
  type User,
} from "@anima/api-client";
import { API_BASE } from "./runtime";

const UNLOCK_TOKEN_KEY = "anima_unlock_token";

export function getUnlockToken(): string | null {
  return localStorage.getItem(UNLOCK_TOKEN_KEY);
}

export function setUnlockToken(token: string): void {
  localStorage.setItem(UNLOCK_TOKEN_KEY, token);
}

export function clearUnlockToken(): void {
  localStorage.removeItem(UNLOCK_TOKEN_KEY);
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
  AgentConfig,
  AgentResponse,
  AuthResponse,
  ChatMessage,
  DailyBrief,
  HomeData,
  LoginResponse,
  MemoryEntry,
  MemoryFile,
  Nudge,
  PersonaTemplate,
  ProviderInfo,
  TaskItem,
  User,
};
