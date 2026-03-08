import { API_BASE } from "./runtime";

interface ApiOptions {
  method?: string;
  body?: unknown;
}

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

async function request<T>(
  endpoint: string,
  options: ApiOptions = {},
): Promise<T> {
  const { method = "GET", body } = options;
  const token = getUnlockToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) headers["x-anima-unlock"] = token;

  const res = await fetch(`${API_BASE}${endpoint}`, {
    method,
    credentials: "include",
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    const message =
      (data as { error?: string; message?: string }).error ||
      (data as { error?: string; message?: string }).message ||
      "Something went wrong";
    throw new Error(message);
  }

  return data as T;
}

export interface User {
  id: number;
  username: string;
  name: string;
  gender?: string;
  age?: number;
  birthday?: string;
  createdAt?: string;
  updatedAt?: string;
}

export interface LoginResponse extends User {
  message: string;
  unlockToken: string;
}

export interface AuthResponse extends User {
  unlockToken: string;
}

export interface ChatMessage {
  id: number;
  userId: number;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  model?: string;
  provider?: string;
  createdAt?: string;
}

export interface AgentResponse {
  response: string;
  model: string;
  provider: string;
  toolsUsed: string[];
}

export interface ProviderInfo {
  name: string;
  defaultModel: string;
  requiresApiKey: boolean;
}

export interface AgentConfig {
  provider: string;
  model: string;
  ollamaUrl?: string;
  hasApiKey: boolean;
  systemPrompt?: string | null;
}

export interface Nudge {
  type: "stale_focus" | "overdue_tasks" | "journal_gap" | "long_absence";
  message: string;
  priority: number;
}

export interface TaskItem {
  id: number;
  userId: number;
  text: string;
  done: boolean;
  priority: number;
  dueDate: string | null;
  completedAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface HomeData {
  currentFocus: string | null;
  tasks: {
    id: number;
    text: string;
    done: boolean;
    priority: number;
    dueDate: string | null;
  }[];
  journalStreak: number;
  journalTotal: number;
  memoryCount: number;
  messageCount: number;
}

export interface DailyBrief {
  message: string;
  context: {
    currentFocus: string | null;
    openTaskCount: number;
    daysSinceLastChat: number | null;
  };
}

export interface MemoryEntry {
  path: string;
  meta: {
    category: string;
    tags: string[];
    created: string;
    updated: string;
    source: string;
  };
  snippet: string;
}

export interface MemoryFile {
  path: string;
  meta: {
    category: string;
    tags: string[];
    created: string;
    updated: string;
    source: string;
  };
  content: string;
}

export const api = {
  auth: {
    login: (username: string, password: string) =>
      request<LoginResponse>("/auth/login", {
        method: "POST",
        body: { username, password },
      }),
    register: (username: string, password: string, name: string) =>
      request<AuthResponse>("/auth/register", {
        method: "POST",
        body: { username, password, name },
      }),
    me: () => request<User>("/auth/me"),
    logout: () => request<{ success: boolean }>("/auth/logout", { method: "POST" }),
  },
  users: {
    me: (id: number) => request<User>(`/users/${id}`),
    update: (id: number, data: Partial<User>) =>
      request<User>(`/users/${id}`, { method: "PUT", body: data }),
    delete: (id: number) =>
      request<{ message: string }>(`/users/${id}`, { method: "DELETE" }),
  },
  chat: {
    send: (message: string, userId: number) =>
      request<AgentResponse>("/chat", {
        method: "POST",
        body: { message, userId, stream: false },
      }),

    stream: async function* (
      message: string,
      userId: number,
    ): AsyncGenerator<string> {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...(getUnlockToken() ? { "x-anima-unlock": getUnlockToken()! } : {}),
        },
        body: JSON.stringify({ message, userId, stream: true }),
      });

      if (!res.ok) {
        const err = await res.text();
        throw new Error(err);
      }

      const reader = res.body?.getReader();
      if (!reader) throw new Error("No response body");

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("event: error")) {
            // Next data line will contain the error
            continue;
          }
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.error) throw new Error(data.error);
              if (data.content) yield data.content;
            } catch (e) {
              if (e instanceof Error && e.message) throw e;
              // skip malformed chunks
            }
          }
        }
      }
    },

    history: (userId: number, limit = 50) =>
      request<ChatMessage[]>(`/chat/history?userId=${userId}&limit=${limit}`),

    clearHistory: (userId: number) =>
      request<{ status: string }>("/chat/history", {
        method: "DELETE",
        body: { userId },
      }),

    brief: (userId: number) =>
      request<DailyBrief>(`/chat/brief?userId=${userId}`),

    nudges: (userId: number) =>
      request<{ nudges: Nudge[] }>(`/chat/nudges?userId=${userId}`),

    home: (userId: number) => request<HomeData>(`/chat/home?userId=${userId}`),

    consolidate: (userId: number) =>
      request<{
        filesProcessed: number;
        filesChanged: number;
        errors: string[];
      }>("/chat/consolidate", { method: "POST", body: { userId } }),
  },
  config: {
    providers: () => request<ProviderInfo[]>("/config/providers"),

    get: (userId: number) => request<AgentConfig>(`/config/${userId}`),

    update: (
      userId: number,
      data: {
        provider: string;
        model: string;
        apiKey?: string;
        ollamaUrl?: string;
        systemPrompt?: string;
      },
    ) =>
      request<{ status: string }>(`/config/${userId}`, {
        method: "PUT",
        body: data,
      }),
  },
  memory: {
    list: (userId: number, section?: string) =>
      request<{ count: number; memories: MemoryEntry[] }>(
        `/memory/${userId}${section && section !== "all" ? `?section=${encodeURIComponent(section)}` : ""}`,
      ),
    search: (userId: number, query: string) =>
      request<{ count: number; results: MemoryEntry[] }>(
        `/memory/${userId}/search?q=${encodeURIComponent(query)}`,
      ),
    read: (userId: number, section: string, filename: string) =>
      request<MemoryFile>(
        `/memory/${userId}/${encodeURIComponent(section)}/${encodeURIComponent(filename)}`,
      ),
    write: (
      userId: number,
      section: string,
      filename: string,
      payload: { content: string; tags?: string[] },
    ) =>
      request<MemoryFile>(
        `/memory/${userId}/${encodeURIComponent(section)}/${encodeURIComponent(filename)}`,
        { method: "PUT", body: payload },
      ),
    remove: (userId: number, section: string, filename: string) =>
      request<{ deleted: boolean }>(
        `/memory/${userId}/${encodeURIComponent(section)}/${encodeURIComponent(filename)}`,
        { method: "DELETE" },
      ),
  },
  tasks: {
    list: (userId: number) => request<TaskItem[]>(`/tasks?userId=${userId}`),

    create: (
      userId: number,
      text: string,
      priority?: number,
      dueDate?: string,
      dueDateRaw?: string,
    ) =>
      request<TaskItem>("/tasks", {
        method: "POST",
        body: { userId, text, priority, dueDate, dueDateRaw },
      }),

    update: (
      id: number,
      data: {
        text?: string;
        done?: boolean;
        priority?: number;
        dueDate?: string | null;
      },
    ) => request<TaskItem>(`/tasks/${id}`, { method: "PUT", body: data }),

    delete: (id: number) =>
      request<{ status: string }>(`/tasks/${id}`, { method: "DELETE" }),
  },
  soul: {
    get: (userId: number) => request<{ content: string; path: string }>(`/soul/${userId}`),
    update: (userId: number, content: string) =>
      request<{ status: string; path: string }>(`/soul/${userId}`, {
        method: "PUT",
        body: { content },
      }),
  },
  vault: {
    export: (passphrase: string) =>
      request<{ filename: string; vault: string; size: number }>("/vault/export", {
        method: "POST",
        body: { passphrase },
      }),
    import: (passphrase: string, vault: string) =>
      request<{ status: string; restoredUsers: number; restoredMemoryFiles: number }>(
        "/vault/import",
        {
          method: "POST",
          body: { passphrase, vault },
        },
      ),
  },
  translate: async (text: string, targetLang: string): Promise<string> => {
    const res = await fetch(
      `https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=${targetLang}&dt=t&q=${encodeURIComponent(text)}`,
    );
    const data = await res.json();
    return data[0].map((s: any[]) => s[0]).join("");
  },
};
