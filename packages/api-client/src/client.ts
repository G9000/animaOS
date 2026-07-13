import type {
  ApiClientOptions,
  AgentConfig,
  AgentBiographyPreviewData,
  AgentProfileData,
  AgentResponse,
  AgentStateData,
  AuthResponse,
  ChangePasswordResponse,
  CorefsCredentialResponse,
  ConfirmRecoveryCredentialResponse,
  PrepareRecoveryCredentialResponse,
  PrepareCorefsRecoveryCredentialResponse,
  ChatMessage,
  ChatContextMessage,
  ChatRequestAttachment,
  CreateThreadResponse,
  DailyBrief,
  DbQueryResult,
  DbTableData,
  DbTableInfo,
  DiaryAttachmentData,
  DiaryEntryCreateData,
  DiaryEntryData,
  DiaryEntryUpdateData,
  DiaryFolderData,
  DocumentWorkflow,
  DocumentWorkflowActionResponse,
  EmotionalContextData,
  GraphEntity,
  GraphEntityDetail,
  GraphOverviewData,
  GraphRelation,
  GraphSearchResult,
  Greeting,
  Reflection,
  HomeData,
  KnowledgeBundleRun,
  KnowledgeConcept,
  KnowledgeConceptSummary,
  KnowledgeImportResponse,
  KnowledgeLintFinding,
  KnowledgeSearchResponse,
  KnowledgeSource,
  KnowledgeSourceResponse,
  LoginResponse,
  MemoryEpisodeData,
  MemoryItemData,
  MemoryOverviewData,
  MemorySearchResult,
  Nudge,
  OllamaModelInfo,
  PendingMemoryConsolidationResponse,
  PendingMemoryOpsResponse,
  PersonaTemplate,
  PersonaTemplateInfo,
  PresenceConfig,
  PresenceConfigUpdate,
  ProactiveNotice,
  ProviderInfo,
  SelfModelData,
  SelfModelSection,
  TaskItem,
  ThreadContextStats,
  ThreadListResponse,
  ThreadMessagesResponse,
  TodayContext,
  TraceEvent,
  TraceMessagePreview,
  User,
  VaultExportResponse,
  VaultImportResponse,
  VaultTransferFormat,
} from "./types";

interface ApiRequestOptions {
  method?: string;
  body?: unknown;
}

type StreamEventPayload = {
  error?: string;
  content?: string;
  output?: string;
  isTerminal?: boolean;
  stepIndex?: number;
  id?: string;
  name?: string;
  arguments?: unknown;
  callId?: string;
  isError?: boolean;
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
  reasoningTokens?: number;
  cachedInputTokens?: number;
  stepDurationMs?: number;
  llmDurationMs?: number;
  ttftMs?: number;
  phase?: "request" | "result";
  messageCount?: number;
  allowedTools?: string[];
  forceToolCall?: boolean;
  messages?: TraceMessagePreview[];
  assistantTextChars?: number;
  assistantTextPreview?: string;
  toolCallCount?: number;
  reasoningChars?: number;
  reasoningCaptured?: boolean;
  code?: string;
  message?: string;
  status?: string;
  stopReason?: string;
  provider?: string;
  model?: string;
  toolsUsed?: string[];
  runId?: number;
  toolName?: string;
  toolCallId?: string;
  threadId?: number;
  retrieval?: AgentResponse["retrieval"];
};

function trimBaseUrl(baseUrl: string): string {
  return baseUrl.replace(/\/$/, "");
}

function parseSseEvent(
  rawEvent: string,
): { event: string; payload: StreamEventPayload } | null {
  let event = "message";
  const dataLines: string[] = [];

  for (const line of rawEvent.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }

  if (dataLines.length === 0) {
    return null;
  }

  try {
    return {
      event,
      payload: JSON.parse(dataLines.join("\n")) as StreamEventPayload,
    };
  } catch {
    return null;
  }
}

function extractErrorMessage(data: unknown, fallback: string): string {
  if (!data || typeof data !== "object") return fallback;

  const payload = data as {
    error?: unknown;
    message?: unknown;
    detail?: unknown;
    details?: unknown;
  };
  const normalizedDetails = extractDetailMessages(payload.details);
  if (normalizedDetails) return normalizedDetails;

  for (const value of [payload.error, payload.message, payload.detail]) {
    if (typeof value === "string" && value.trim()) return value;
  }
  const detailMessages = extractDetailMessages(payload.detail);
  if (detailMessages) return detailMessages;

  return fallback;
}

function extractDetailMessages(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value;
  if (Array.isArray(value)) {
    const details = value
      .map((item) => {
        if (!item || typeof item !== "object") return null;
        const msg = (item as { msg?: unknown }).msg;
        return typeof msg === "string" && msg.trim() ? msg : null;
      })
      .filter((msg): msg is string => msg !== null);
    if (details.length > 0) return details.join("; ");
  }
  return null;
}

export function createApiClient(options: ApiClientOptions) {
  const {
    baseUrl,
    getUnlockToken,
    getNonce,
    fetchImpl = fetch,
    credentials = "include",
  } = options;
  const normalizedBaseUrl = trimBaseUrl(baseUrl);

  async function request<T>(
    endpoint: string,
    requestOptions: ApiRequestOptions = {},
  ): Promise<T> {
    const { method = "GET", body } = requestOptions;
    const token = getUnlockToken?.() || null;
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };

    if (token) {
      headers["x-anima-unlock"] = token;
    }

    const nonce = getNonce?.() || null;
    if (nonce) {
      headers["x-anima-nonce"] = nonce;
    }

    const response = await fetchImpl(`${normalizedBaseUrl}${endpoint}`, {
      method,
      credentials,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(extractErrorMessage(data, "Something went wrong"));
    }

    return data as T;
  }

  async function uploadFile<T>(
    endpoint: string,
    file: File | Blob,
    fieldName = "file",
    fields: Record<string, string> = {},
  ): Promise<T> {
    const token = getUnlockToken?.() || null;
    const headers: Record<string, string> = {};
    if (token) {
      headers["x-anima-unlock"] = token;
    }
    const nonce = getNonce?.() || null;
    if (nonce) {
      headers["x-anima-nonce"] = nonce;
    }

    const form = new FormData();
    for (const [key, value] of Object.entries(fields)) {
      form.append(key, value);
    }
    form.append(fieldName, file);

    const response = await fetchImpl(`${normalizedBaseUrl}${endpoint}`, {
      method: "POST",
      credentials,
      headers,
      body: form,
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(extractErrorMessage(data, "Something went wrong"));
    }
    return data as T;
  }

  async function downloadBlob(endpoint: string): Promise<Blob> {
    const token = getUnlockToken?.() || null;
    const headers: Record<string, string> = {};
    if (token) {
      headers["x-anima-unlock"] = token;
    }
    const nonce = getNonce?.() || null;
    if (nonce) {
      headers["x-anima-nonce"] = nonce;
    }

    const response = await fetchImpl(`${normalizedBaseUrl}${endpoint}`, {
      method: "GET",
      credentials,
      headers,
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(extractErrorMessage(data, "Something went wrong"));
    }

    return response.blob();
  }

  async function* streamChat(
    message: string,
    userId: number,
    threadId?: number,
    attachments: ChatRequestAttachment[] = [],
    contextMessages: ChatContextMessage[] = [],
    todayContext?: TodayContext | null,
    documentIds: number[] = [],
  ): AsyncGenerator<string> {
    const token = getUnlockToken?.() || null;
    const streamNonce = getNonce?.() || null;
    const response = await fetchImpl(`${normalizedBaseUrl}/chat`, {
      method: "POST",
      credentials,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { "x-anima-unlock": token } : {}),
        ...(streamNonce ? { "x-anima-nonce": streamNonce } : {}),
      },
      body: JSON.stringify({
        message,
        userId,
        stream: true,
        ...(threadId !== undefined ? { threadId } : {}),
        ...(attachments.length > 0 ? { attachments } : {}),
        ...(contextMessages.length > 0 ? { contextMessages } : {}),
        ...(todayContext ? { todayContext } : {}),
        ...(documentIds.length > 0 ? { documentIds } : {}),
      }),
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(
        extractErrorMessage(data, response.statusText || "Something went wrong"),
      );
    }

    const reader = response.body?.getReader();
    if (!reader) throw new Error("No response body");

    const decoder = new TextDecoder();
    let buffer = "";
    let sawVisibleContent = false;
    let terminalToolOutput: string | null = null;
    let emittedTerminalToolOutput = false;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

      let delimiterIndex = buffer.indexOf("\n\n");
      while (delimiterIndex !== -1) {
        const rawEvent = buffer.slice(0, delimiterIndex);
        buffer = buffer.slice(delimiterIndex + 2);
        delimiterIndex = buffer.indexOf("\n\n");

        const parsedEvent = parseSseEvent(rawEvent);
        if (!parsedEvent) continue;

        const { event, payload } = parsedEvent;
        if (payload.error) {
          throw new Error(payload.error);
        }

        if (
          event === "reasoning" &&
          typeof payload.content === "string" &&
          payload.content
        ) {
          yield `\x00REASONING\x00${payload.content}`;
          continue;
        }

        if (
          event === "chunk" &&
          typeof payload.content === "string" &&
          payload.content
        ) {
          sawVisibleContent = true;
          yield payload.content;
          continue;
        }

        if (event === "tool_call") {
          const traceEvent: TraceEvent = {
            type: "tool_call",
            stepIndex: payload.stepIndex,
            name: payload.name,
            arguments: payload.arguments,
            callId: payload.id,
          };
          yield `\x00TRACE\x00${JSON.stringify(traceEvent)}`;
        }

        if (event === "step_state") {
          const traceEvent: TraceEvent = {
            type: "step_state",
            stepIndex: payload.stepIndex,
            phase: payload.phase,
            messageCount: payload.messageCount,
            allowedTools: payload.allowedTools,
            forceToolCall: payload.forceToolCall,
            messages: payload.messages,
            assistantTextChars: payload.assistantTextChars,
            assistantTextPreview: payload.assistantTextPreview,
            toolCallCount: payload.toolCallCount,
            reasoningChars: payload.reasoningChars,
            reasoningCaptured: payload.reasoningCaptured,
          };
          yield `\x00TRACE\x00${JSON.stringify(traceEvent)}`;
          continue;
        }

        if (event === "warning") {
          const traceEvent: TraceEvent = {
            type: "warning",
            stepIndex: payload.stepIndex,
            code: payload.code,
            message: payload.message,
          };
          yield `\x00TRACE\x00${JSON.stringify(traceEvent)}`;
          continue;
        }

        if (event === "tool_return") {
          const traceEvent: TraceEvent = {
            type: "tool_return",
            stepIndex: payload.stepIndex,
            name: payload.name,
            callId: payload.callId,
            output: payload.output,
            isError: payload.isError,
          };
          yield `\x00TRACE\x00${JSON.stringify(traceEvent)}`;

          if (
            payload.isTerminal === true &&
            typeof payload.output === "string" &&
            payload.output
          ) {
            terminalToolOutput = payload.output;
          }
          continue;
        }

        if (event === "usage") {
          const traceEvent: TraceEvent = {
            type: "usage",
            promptTokens: payload.promptTokens,
            completionTokens: payload.completionTokens,
            totalTokens: payload.totalTokens,
            reasoningTokens: payload.reasoningTokens,
            cachedInputTokens: payload.cachedInputTokens,
          };
          yield `\x00TRACE\x00${JSON.stringify(traceEvent)}`;
          continue;
        }

        if (event === "timing") {
          const traceEvent: TraceEvent = {
            type: "timing",
            stepIndex: payload.stepIndex,
            stepDurationMs: payload.stepDurationMs,
            llmDurationMs: payload.llmDurationMs,
            ttftMs: payload.ttftMs,
          };
          yield `\x00TRACE\x00${JSON.stringify(traceEvent)}`;
          continue;
        }

        if (event === "done") {
          const traceEvent: TraceEvent = {
            type: "done",
            status: payload.status,
            stopReason: payload.stopReason,
            provider: payload.provider,
            model: payload.model,
            toolsUsed: payload.toolsUsed,
            threadId: payload.threadId,
            retrieval: payload.retrieval,
          };
          yield `\x00TRACE\x00${JSON.stringify(traceEvent)}`;

          if (terminalToolOutput && !emittedTerminalToolOutput) {
            emittedTerminalToolOutput = true;
            if (!sawVisibleContent) {
              yield terminalToolOutput;
            } else {
              yield `\x00CONTENT_RESET\x00${terminalToolOutput}`;
            }
          }
        }

        if (event === "approval_pending") {
          const traceEvent: TraceEvent = {
            type: "approval_pending",
            runId: payload.runId,
            name: payload.toolName,
            callId: payload.toolCallId,
            arguments: payload.arguments,
          };
          yield `\x00TRACE\x00${JSON.stringify(traceEvent)}`;
          continue;
        }

        if (event === "cancelled") {
          const traceEvent: TraceEvent = {
            type: "cancelled",
            runId: payload.runId,
          };
          yield `\x00TRACE\x00${JSON.stringify(traceEvent)}`;
        }
      }
    }

    if (terminalToolOutput && !emittedTerminalToolOutput) {
      if (!sawVisibleContent) {
        yield terminalToolOutput;
      } else {
        yield `\x00CONTENT_RESET\x00${terminalToolOutput}`;
      }
    }
  }

  return {
    auth: {
      login: (username: string, password: string) =>
        request<LoginResponse>("/auth/login", {
          method: "POST",
          body: { username, password },
        }),
      register: (
        username: string,
        password: string,
        name: string,
        personaTemplate: PersonaTemplate = "default",
        agentName: string = "Anima",
        userDirective: string = "",
        relationship: string = "companion",
      ) =>
        request<AuthResponse>("/auth/register", {
          method: "POST",
          body: {
            username,
            password,
            name,
            personaTemplate,
            agentName,
            userDirective,
            relationship,
          },
        }),
      createAiChat: (
        messages: { role: string; content: string }[],
        ownerName: string,
      ) =>
        request<{
          message: string;
          done: boolean;
          soulData?: Record<string, string>;
        }>("/auth/create-ai/chat", {
          method: "POST",
          body: { messages, ownerName },
        }),
      me: () => request<User>("/auth/me"),
      logout: () =>
        request<{ success: boolean }>("/auth/logout", { method: "POST" }),
      changePassword: (
        oldPassword: string,
        newPassword: string,
        scope: "full" | "soul" | "fs" = "full",
      ) =>
        request<ChangePasswordResponse>("/auth/change-password", {
          method: "POST",
          body: { oldPassword, newPassword, scope },
        }),
      changeCorefsPassword: (currentPassword: string, newPassword: string) =>
        request<CorefsCredentialResponse>("/auth/corefs/change-password", {
          method: "POST",
          body: { currentPassword, newPassword },
        }),
      recover: (
        recoveryPhrase: string,
        newPassword: string,
        scope: "full" | "soul" | "fs" = "full",
      ) =>
        request<LoginResponse>("/auth/recover", {
          method: "POST",
          body: { recoveryPhrase, newPassword, scope },
        }),
      prepareRecoveryCredential: (
        currentRecoveryPhrase: string,
        currentPassword: string,
        scope: "full" | "soul" | "fs" = "full",
      ) =>
        request<PrepareRecoveryCredentialResponse>(
          "/auth/recovery-credential/prepare",
          {
            method: "POST",
            body: { currentRecoveryPhrase, currentPassword, scope },
          },
        ),
      prepareCorefsRecoveryCredential: (
        currentRecoveryPhrase: string,
        currentPassword: string,
      ) =>
        request<PrepareCorefsRecoveryCredentialResponse>(
          "/auth/corefs/recovery-credential/prepare",
          {
            method: "POST",
            body: { currentRecoveryPhrase, currentPassword },
          },
        ),
      confirmRecoveryCredential: (
        recoveryPhrase: string,
        pendingGeneration: number,
        scope: "full" | "soul" | "fs",
        currentPassword: string,
      ) =>
        request<ConfirmRecoveryCredentialResponse>(
          "/auth/recovery-credential/confirm",
          {
            method: "POST",
            body: { recoveryPhrase, pendingGeneration, scope, currentPassword },
          },
        ),
      confirmCorefsRecoveryCredential: (
        recoveryPhrase: string,
        pendingGeneration: number,
      ) =>
        request<CorefsCredentialResponse>(
          "/auth/corefs/recovery-credential/confirm",
          {
            method: "POST",
            body: { recoveryPhrase, pendingGeneration },
          },
        ),
    },
    users: {
      me: (id: number) => request<User>(`/users/${id}`),
      update: (id: number, data: Partial<User>) =>
        request<User>(`/users/${id}`, { method: "PUT", body: data }),
      delete: (id: number) =>
        request<{ message: string }>(`/users/${id}`, { method: "DELETE" }),
    },
    chat: {
      send: (
        message: string,
        userId: number,
        threadId?: number,
        attachments: ChatRequestAttachment[] = [],
        contextMessages: ChatContextMessage[] = [],
        todayContext?: TodayContext | null,
        documentIds: number[] = [],
      ) =>
        request<AgentResponse>("/chat", {
          method: "POST",
          body: {
            message,
            userId,
            stream: false,
            ...(threadId !== undefined ? { threadId } : {}),
            ...(attachments.length > 0 ? { attachments } : {}),
            ...(contextMessages.length > 0 ? { contextMessages } : {}),
            ...(todayContext ? { todayContext } : {}),
            ...(documentIds.length > 0 ? { documentIds } : {}),
          },
        }),
      stream: (
        message: string,
        userId: number,
        threadId?: number,
        attachments: ChatRequestAttachment[] = [],
        contextMessages: ChatContextMessage[] = [],
        todayContext?: TodayContext | null,
        documentIds: number[] = [],
      ) =>
        streamChat(
          message,
          userId,
          threadId,
          attachments,
          contextMessages,
          todayContext,
          documentIds,
        ),
      history: (userId: number, limit = 50) =>
        request<ChatMessage[]>(`/chat/history?userId=${userId}&limit=${limit}`),
      clearHistory: (userId: number) =>
        request<{ status: string }>("/chat/history", {
          method: "DELETE",
          body: { userId },
        }),
      brief: (userId: number) =>
        request<DailyBrief>(`/chat/brief?userId=${userId}`),
      greeting: (userId: number) =>
        request<Greeting>(`/chat/greeting?userId=${userId}`),
      reflection: (userId: number) =>
        request<Reflection>(`/chat/reflection?userId=${userId}`),
      nudges: (userId: number) =>
        request<{ nudges: Nudge[] }>(`/chat/nudges?userId=${userId}`),
      proactiveNotice: (userId: number, instruction?: string) => {
        const params = new URLSearchParams({ userId: String(userId) });
        const trimmed = instruction?.trim();
        if (trimmed) params.set("instruction", trimmed);
        return request<{ notice: ProactiveNotice | null }>(
          `/chat/proactive-notice?${params.toString()}`,
        );
      },
      home: (userId: number) =>
        request<HomeData>(`/chat/home?userId=${userId}`),
      consolidate: (userId: number) =>
        request<{
          filesProcessed: number;
          filesChanged: number;
          errors: string[];
        }>("/chat/consolidate", { method: "POST", body: { userId } }),
      sleep: (userId: number) =>
        request<Record<string, unknown>>("/chat/sleep", {
          method: "POST",
          body: { userId },
        }),
      reflect: (userId: number) =>
        request<Record<string, unknown>>("/chat/reflect", {
          method: "POST",
          body: { userId },
        }),
    },
    documents: {
      uploadPdf: (userId: number, file: File | Blob, threadId?: number) =>
        uploadFile<DocumentWorkflowActionResponse>(
          "/documents/pdf",
          file,
          "file",
          {
            userId: String(userId),
            ...(threadId !== undefined ? { threadId: String(threadId) } : {}),
          },
        ),
      getWorkflow: (workflowId: number) =>
        request<DocumentWorkflow>(`/documents/workflows/${workflowId}`),
      resumeWorkflow: (workflowId: number) =>
        request<DocumentWorkflowActionResponse>(
          `/documents/workflows/${workflowId}/resume`,
          { method: "POST" },
        ),
    },
    knowledge: {
      listSources: (userId: number, limit?: number) => {
        const params = new URLSearchParams({ userId: String(userId) });
        if (limit !== undefined) params.set("limit", String(limit));
        return request<{ sources: KnowledgeSource[] }>(
          `/knowledge/sources?${params.toString()}`,
        );
      },
      readSource: (userId: number, sourceId: number) =>
        request<KnowledgeSourceResponse>(
          `/knowledge/sources/${sourceId}?userId=${userId}`,
        ),
      listConcepts: (userId: number, limit?: number) => {
        const params = new URLSearchParams({ userId: String(userId) });
        if (limit !== undefined) params.set("limit", String(limit));
        return request<{ concepts: KnowledgeConceptSummary[] }>(
          `/knowledge/concepts?${params.toString()}`,
        );
      },
      readConcept: (userId: number, conceptId: number) =>
        request<KnowledgeConcept>(
          `/knowledge/concepts/${conceptId}?userId=${userId}`,
        ),
      compileSource: (userId: number, sourceId: number) =>
        request<{ compileRun: KnowledgeBundleRun }>(
          `/knowledge/sources/${sourceId}/compile?userId=${userId}`,
          {
            method: "POST",
          },
        ),
      search: (userId: number, query: string, limit?: number) => {
        const params = new URLSearchParams({
          userId: String(userId),
          q: query,
        });
        if (limit !== undefined) params.set("limit", String(limit));
        return request<KnowledgeSearchResponse>(
          `/knowledge/search?${params.toString()}`,
        );
      },
      runLint: (
        userId: number,
        options?: { sourceId?: number; conceptId?: number },
      ) =>
        request<{ findings: KnowledgeLintFinding[] }>("/knowledge/lint", {
          method: "POST",
          body: {
            userId,
            ...(options?.sourceId !== undefined
              ? { sourceId: options.sourceId }
              : {}),
            ...(options?.conceptId !== undefined
              ? { conceptId: options.conceptId }
              : {}),
          },
        }),
      exportBundle: (userId: number) =>
        downloadBlob(`/knowledge/export?userId=${userId}`),
      importBundle: (userId: number, file: File | Blob) =>
        uploadFile<KnowledgeImportResponse>(
          `/knowledge/import?userId=${userId}`,
          file,
        ),
    },
    config: {
      providers: () => request<ProviderInfo[]>("/config/providers"),
      ollamaModels: (baseUrl?: string) =>
        request<OllamaModelInfo[]>(
          `/config/ollama-models${
            baseUrl ? `?baseUrl=${encodeURIComponent(baseUrl)}` : ""
          }`,
        ),
      personaTemplates: () =>
        request<PersonaTemplateInfo[]>("/config/persona-templates"),
      get: (userId: number) => request<AgentConfig>(`/config/${userId}`),
      update: (
        userId: number,
        data: {
          provider: string;
          model: string;
          extractionModel?: string;
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
    presence: {
      get: (userId: number) =>
        request<PresenceConfig>(`/presence/${userId}`),
      update: (userId: number, data: PresenceConfigUpdate) =>
        request<PresenceConfig>(`/presence/${userId}`, {
          method: "PUT",
          body: data,
        }),
    },
    graph: {
      overview: (userId: number) =>
        request<GraphOverviewData>(`/graph/${userId}/overview`),
      entities: (
        userId: number,
        options?: {
          type?: string;
          search?: string;
          limit?: number;
          offset?: number;
        },
      ) => {
        const params = new URLSearchParams();
        if (options?.type) params.set("type", options.type);
        if (options?.search) params.set("search", options.search);
        if (options?.limit) params.set("limit", String(options.limit));
        if (options?.offset) params.set("offset", String(options.offset));
        const qs = params.toString();
        return request<{ total: number; entities: GraphEntity[] }>(
          `/graph/${userId}/entities${qs ? `?${qs}` : ""}`,
        );
      },
      entity: (userId: number, entityId: number) =>
        request<GraphEntityDetail>(`/graph/${userId}/entities/${entityId}`),
      relations: (
        userId: number,
        options?: {
          entityId?: number;
          type?: string;
          limit?: number;
        },
      ) => {
        const params = new URLSearchParams();
        if (options?.entityId)
          params.set("entity_id", String(options.entityId));
        if (options?.type) params.set("type", options.type);
        if (options?.limit) params.set("limit", String(options.limit));
        const qs = params.toString();
        return request<{ relations: GraphRelation[] }>(
          `/graph/${userId}/relations${qs ? `?${qs}` : ""}`,
        );
      },
      search: (userId: number, query: string, maxDepth = 2, limit = 20) =>
        request<GraphSearchResult>(
          `/graph/${userId}/search?q=${encodeURIComponent(query)}&max_depth=${maxDepth}&limit=${limit}`,
        ),
      context: (userId: number, query: string, limit = 10) =>
        request<{ query: string; context: string[]; count: number }>(
          `/graph/${userId}/context?q=${encodeURIComponent(query)}&limit=${limit}`,
        ),
    },
    diary: {
      list: (userId: number, limit = 50) =>
        request<DiaryEntryData[]>(`/diary?userId=${userId}&limit=${limit}`),
      create: (userId: number, data: DiaryEntryCreateData) =>
        request<DiaryEntryData>("/diary", {
          method: "POST",
          body: { userId, ...data },
        }),
      update: (entryId: number, data: DiaryEntryUpdateData) =>
        request<DiaryEntryData>(`/diary/${entryId}`, {
          method: "PATCH",
          body: data,
        }),
      uploadAttachment: (entryId: number, file: File | Blob, caption?: string) =>
        uploadFile<DiaryAttachmentData>(
          `/diary/${entryId}/attachments`,
          file,
          "file",
          caption?.trim() ? { caption: caption.trim() } : {},
        ),
      downloadAttachment: (entryId: number, attachmentId: number) =>
        downloadBlob(`/diary/${entryId}/attachments/${attachmentId}`),
      delete: (entryId: number) =>
        request<{ deleted: boolean }>(`/diary/${entryId}`, {
          method: "DELETE",
        }),
      folders: {
        list: (userId: number) =>
          request<DiaryFolderData[]>(`/diary/folders?userId=${userId}`),
        create: (userId: number, name: string) =>
          request<DiaryFolderData>("/diary/folders", {
            method: "POST",
            body: { userId, name },
          }),
        rename: (folderId: number, name: string) =>
          request<DiaryFolderData>(`/diary/folders/${folderId}`, {
            method: "PATCH",
            body: { name },
          }),
        delete: (folderId: number) =>
          request<{ deleted: boolean }>(`/diary/folders/${folderId}`, {
            method: "DELETE",
          }),
      },
    },
    memory: {
      overview: (userId: number) =>
        request<MemoryOverviewData>(`/memory/${userId}`),
      listItems: (userId: number, category?: string) =>
        request<MemoryItemData[]>(
          `/memory/${userId}/items${category ? `?category=${encodeURIComponent(category)}` : ""}`,
        ),
      createItem: (
        userId: number,
        data: { content: string; category?: string; importance?: number },
      ) =>
        request<MemoryItemData>(`/memory/${userId}/items`, {
          method: "POST",
          body: data,
        }),
      updateItem: (
        userId: number,
        itemId: number,
        data: { content?: string; category?: string; importance?: number },
      ) =>
        request<MemoryItemData>(`/memory/${userId}/items/${itemId}`, {
          method: "PUT",
          body: data,
        }),
      deleteItem: (userId: number, itemId: number) =>
        request<{ deleted: boolean }>(`/memory/${userId}/items/${itemId}`, {
          method: "DELETE",
        }),
      listEpisodes: (userId: number, limit = 20) =>
        request<MemoryEpisodeData[]>(
          `/memory/${userId}/episodes?limit=${limit}`,
        ),
      search: (userId: number, query: string) =>
        request<{ count: number; results: MemorySearchResult[] }>(
          `/memory/${userId}/search?q=${encodeURIComponent(query)}`,
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
      get: (userId: number) =>
        request<{ content: string; source: string }>(`/soul/${userId}`),
      update: (userId: number, content: string) =>
        request<{ status: string }>(`/soul/${userId}`, {
          method: "PUT",
          body: { content },
        }),
    },
    consciousness: {
      getSelfModel: (userId: number) =>
        request<SelfModelData>(`/consciousness/${userId}/self-model`),
      getPendingOps: (userId: number) =>
        request<PendingMemoryOpsResponse>(
          `/consciousness/${userId}/pending-ops`,
        ),
      consolidatePendingOps: (userId: number) =>
        request<PendingMemoryConsolidationResponse>(
          `/consciousness/${userId}/pending-ops/consolidate`,
          { method: "POST" },
        ),
      getSelfModelSection: (userId: number, section: string) =>
        request<SelfModelSection>(
          `/consciousness/${userId}/self-model/${section}`,
        ),
      updateSelfModelSection: (
        userId: number,
        section: string,
        content: string,
        options?: { allowIdentityOverride?: boolean },
      ) =>
        request<SelfModelSection>(
          `/consciousness/${userId}/self-model/${section}`,
          {
            method: "PUT",
            body: {
              content,
              ...(options?.allowIdentityOverride !== undefined
                ? { allowIdentityOverride: options.allowIdentityOverride }
                : {}),
            },
          },
        ),
      getEmotions: (userId: number, limit = 10) =>
        request<EmotionalContextData>(
          `/consciousness/${userId}/emotions?limit=${limit}`,
        ),
      getAgentState: (userId: number) =>
        request<AgentStateData>(`/consciousness/${userId}/agent-state`),
      getIntentions: (userId: number) =>
        request<{ content: string }>(`/consciousness/${userId}/intentions`),
      getAgentProfile: (userId: number) =>
        request<AgentProfileData>(`/consciousness/${userId}/agent-profile`),
      getAgentBiographyPreview: (userId: number) =>
        request<AgentBiographyPreviewData>(
          `/consciousness/${userId}/agent-biography-preview`,
        ),
      updateAgentProfile: (
        userId: number,
        data: {
          agentName?: string;
          relationship?: string;
          personaTemplate?: string;
          agentBirthday?: string;
          thinkingMonologue?: string[];
          allowIdentityOverride?: boolean;
        },
      ) =>
        request<AgentProfileData>(`/consciousness/${userId}/agent-profile`, {
          method: "PATCH",
          body: data,
        }),
      generateThinkingMonologue: (userId: number) =>
        request<{ thinkingMonologue: string[] }>(
          `/consciousness/${userId}/agent-profile/thinking-monologue/generate`,
          { method: "POST" },
        ),
      uploadAgentAvatar: (userId: number, file: File | Blob) =>
        uploadFile<{ avatarUrl: string }>(
          `/consciousness/${userId}/agent-profile/avatar`,
          file,
        ),
      deleteAgentAvatar: (userId: number) =>
        request<{ avatarUrl: null }>(
          `/consciousness/${userId}/agent-profile/avatar`,
          {
            method: "DELETE",
          },
        ),
      getAgentAvatarUrl: (userId: number) =>
        `${normalizedBaseUrl}/api/consciousness/${userId}/agent-profile/avatar`,
    },
    vault: {
      export: (
        passphrase: string,
        options?: {
          scope?: "full" | "memories";
          format?: VaultTransferFormat;
        },
      ) =>
        request<VaultExportResponse>("/vault/export", {
          method: "POST",
          body: {
            passphrase,
            scope: options?.scope,
            format: options?.format,
          },
        }),
      import: (
        passphrase: string,
        vault: string,
        options?: {
          format?: VaultTransferFormat;
        },
      ) =>
        request<VaultImportResponse>("/vault/import", {
          method: "POST",
          body: {
            passphrase,
            vault,
            format: options?.format,
          },
        }),
    },
    images: {
      removeFromMessage: (messageId: number, attachmentId: string) =>
        request<{
          status: string;
          imageAssetId: number | null;
          assetDeleted: boolean;
          fileDeleted: boolean;
        }>(
          `/images/messages/${messageId}/attachments/${encodeURIComponent(attachmentId)}`,
          { method: "DELETE" },
        ),
      forget: (imageAssetId: number) =>
        request<{
          status: string;
          imageAssetId: number;
          fileDeleted: boolean;
        }>(`/images/${imageAssetId}`, { method: "DELETE" }),
      setRetention: (
        imageAssetId: number,
        retentionState: "transient" | "retained" | "durable",
      ) =>
        request<{
          status: string;
          imageAssetId: number;
          retentionState: string;
        }>(`/images/${imageAssetId}/retention`, {
          method: "PATCH",
          body: { retentionState },
        }),
    },
    threads: {
      list: () => request<ThreadListResponse>("/threads"),
      create: () =>
        request<CreateThreadResponse>("/threads", { method: "POST" }),
      messages: (threadId: number) =>
        request<ThreadMessagesResponse>(`/threads/${threadId}/messages`),
      close: (threadId: number) =>
        request<{ status: string; threadId: number }>(
          `/threads/${threadId}/close`,
          {
            method: "POST",
          },
        ),
      delete: (threadId: number) =>
        request<{ status: string; threadId: number }>(`/threads/${threadId}`, {
          method: "DELETE",
        }),
      contextStats: (threadId: number) =>
        request<ThreadContextStats>(`/threads/${threadId}/context-stats`),
    },
    system: {
      health: () =>
        request<{
          status: string;
          service?: string;
          environment?: string;
          provisioned?: boolean;
        }>("/health"),
    },
    db: {
      verifyPassword: (password: string) =>
        request<{ verified: boolean }>("/db/verify-password", {
          method: "POST",
          body: { password },
        }),
      tables: () => request<DbTableInfo[]>("/db/tables"),
      tableRows: (tableName: string, limit = 100, offset = 0) =>
        request<DbTableData>(
          `/db/tables/${encodeURIComponent(tableName)}?limit=${limit}&offset=${offset}`,
        ),
      tableSchema: (tableName: string) =>
        request<{
          columns: {
            name: string;
            type: string;
            nullable: boolean;
            default: string | null;
            primaryKey: boolean;
          }[];
          indexes: string[];
        }>(`/db/tables/${encodeURIComponent(tableName)}/schema`),
      query: (sql: string) =>
        request<DbQueryResult>("/db/query", {
          method: "POST",
          body: { sql },
        }),
      deleteRow: (tableName: string, conditions: Record<string, unknown>) =>
        request<{ deleted: number }>(
          `/db/tables/${encodeURIComponent(tableName)}/rows`,
          { method: "DELETE", body: { conditions } },
        ),
      updateRow: (
        tableName: string,
        conditions: Record<string, unknown>,
        updates: Record<string, unknown>,
      ) =>
        request<{ updated: number }>(
          `/db/tables/${encodeURIComponent(tableName)}/rows`,
          { method: "PUT", body: { conditions, updates } },
        ),
    },
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;
