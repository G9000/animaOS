const API_BASE = "http://localhost:3031/api";

interface ApiOptions {
  method?: string;
  body?: unknown;
}

async function request<T>(
  endpoint: string,
  options: ApiOptions = {},
): Promise<T> {
  const { method = "GET", body } = options;

  const res = await fetch(`${API_BASE}${endpoint}`, {
    method,
    headers: {
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  const data = await res.json();

  if (!res.ok) {
    throw new Error(data.error || "Something went wrong");
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

export interface DailyBrief {
  message: string;
  context: {
    currentFocus: string | null;
    openTaskCount: number;
    daysSinceLastChat: number | null;
  };
}

export const api = {
  auth: {
    login: (username: string, password: string) =>
      request<LoginResponse>("/auth/login", {
        method: "POST",
        body: { username, password },
      }),
    register: (username: string, password: string, name: string) =>
      request<User>("/auth/register", {
        method: "POST",
        body: { username, password, name },
      }),
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
        headers: { "Content-Type": "application/json" },
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

    consolidate: (userId: number) =>
      request<{ filesProcessed: number; filesChanged: number; errors: string[] }>(
        "/chat/consolidate",
        { method: "POST", body: { userId } },
      ),
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
  translate: async (text: string, targetLang: string): Promise<string> => {
    const res = await fetch(
      `https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=${targetLang}&dt=t&q=${encodeURIComponent(text)}`,
    );
    const data = await res.json();
    return data[0].map((s: any[]) => s[0]).join("");
  },
};
