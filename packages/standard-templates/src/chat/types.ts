// Chat types shared between server and client

export type MessageRole = "user" | "assistant" | "system" | "tool";

export interface MessagePill {
  kind: string;
  label: string;
  ref?: string | number | null;
}

export interface ChatMessage {
  id: number;
  userId: number;
  role: MessageRole;
  content: string;
  createdAt?: string;
  reasoning?: string;
  traceEvents?: TraceEvent[];
  retrieval?: RetrievalTrace | null;
  attachments?: ChatAttachment[];
  source?: string | null;
  pills?: MessagePill[];
}

export type TraceEventType =
  | "step_state"
  | "warning"
  | "tool_call"
  | "tool_return"
  | "usage"
  | "timing"
  | "done"
  | "approval_pending"
  | "cancelled"
  | "memory_state";

export interface RetrievalCitation {
  index: number;
  memoryItemId: number;
  uri: string;
  score?: number | null;
  category?: string | null;
}

export interface RetrievalContextFragment {
  rank: number;
  memoryItemId: number;
  uri: string;
  text: string;
  score?: number | null;
  category?: string | null;
}

export interface RetrievalStats {
  retrievalMs?: number | null;
  totalConsidered: number;
  returned: number;
  cutoffIndex: number;
  cutoffScore?: number | null;
  topScore?: number | null;
  cutoffRatio?: number | null;
  triggeredBy: string;
}

export interface RetrievalSourcePlan {
  source: string;
  query: string;
  mode: string;
  limit: number;
  weight?: number;
  filters?: Record<string, unknown>;
  available?: boolean;
  reason?: string;
}

export interface RetrievalQueryPlan {
  route: string;
  query: string;
  rationale?: string;
  sources: RetrievalSourcePlan[];
}

export interface RetrievalTrace {
  retriever: string;
  citations: RetrievalCitation[];
  contextFragments: RetrievalContextFragment[];
  stats?: RetrievalStats | null;
  queryPlan?: RetrievalQueryPlan | null;
}

export interface ChatAttachment {
  id: string;
  kind: "image";
  mimeType: string;
  filename?: string | null;
  sizeBytes?: number | null;
  url: string;
}

export interface TraceEvent {
  type: TraceEventType;
  stepIndex?: number;
  phase?: "request" | "result";
  messageCount?: number;
  allowedTools?: string[];
  forceToolCall?: boolean;
  messages?: unknown[];
  assistantTextChars?: number;
  assistantTextPreview?: string;
  toolCallCount?: number;
  reasoningChars?: number;
  reasoningCaptured?: boolean;
  code?: string;
  message?: string;
  name?: string;
  arguments?: unknown;
  output?: string;
  isError?: boolean;
  promptTokens?: number;
  completionTokens?: number;
  reasoningTokens?: number;
  cachedInputTokens?: number;
  totalTokens?: number;
  ttftMs?: number;
  llmDurationMs?: number;
  stepDurationMs?: number;
  provider?: string;
  model?: string;
  toolsUsed?: string[];
  stopReason?: string;
  runId?: number;
  callId?: string;
  threadId?: number;
  retrieval?: RetrievalTrace | null;
}

export interface Thread {
  id: number;
  title?: string;
  createdAt?: string;
  lastMessageAt?: string;
  status?: "active" | "closed";
}
