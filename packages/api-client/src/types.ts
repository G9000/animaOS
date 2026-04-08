export interface ApiClientOptions {
  baseUrl: string;
  getUnlockToken?: () => string | null;
  getNonce?: () => string | null;
  fetchImpl?: typeof fetch;
  credentials?: RequestCredentials;
}

export interface User {
  id: number;
  username: string;
  name: string;
  gender?: string | null;
  age?: number | null;
  birthday?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface LoginResponse extends User {
  message: string;
  unlockToken: string;
}

export interface AuthResponse extends User {
  unlockToken: string;
  recoveryPhrase?: string;
}

export interface ChangePasswordResponse {
  success: boolean;
  unlockToken: string;
}

export type VaultTransferFormat = "vault_json" | "anima_capsule";

export interface VaultExportResponse {
  filename: string;
  vault: string;
  size: number;
  format?: VaultTransferFormat;
}

export interface VaultImportResponse {
  status: string;
  restoredUsers: number;
  restoredMemoryFiles: number;
  requiresReauth?: boolean;
  format?: VaultTransferFormat;
}

export interface PersonaTemplateInfo {
  id: string;
  name: string;
  description: string;
  defaultAvatarUrl?: string | null;
}

export type PersonaTemplate = "default" | "companion" | "mirror" | "anima";

export interface AgentProfileData {
  agentName: string;
  relationship: string;
  personaTemplate: string;
  agentType?: string;
  avatarUrl?: string | null;
  setupComplete: boolean;
}

export interface TraceMessagePreview {
  role: string;
  chars: number;
  preview: string;
  toolName?: string;
  toolCallId?: string;
  toolCallCount?: number;
}

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

export interface RetrievalTrace {
  retriever: string;
  citations: RetrievalCitation[];
  contextFragments: RetrievalContextFragment[];
  stats?: RetrievalStats | null;
}

export interface TraceEvent {
  type:
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
  stepIndex?: number;
  phase?: "request" | "result";
  messageCount?: number;
  allowedTools?: string[];
  forceToolCall?: boolean;
  messages?: TraceMessagePreview[];
  toolSchemas?: Record<string, unknown>;
  assistantTextChars?: number;
  assistantTextPreview?: string;
  toolCallCount?: number;
  reasoningChars?: number;
  reasoningCaptured?: boolean;
  code?: string;
  message?: string;
  name?: string;
  arguments?: unknown;
  callId?: string;
  output?: string;
  isError?: boolean;
  toolSucceeded?: boolean;
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
  reasoningTokens?: number;
  cachedInputTokens?: number;
  stepDurationMs?: number;
  llmDurationMs?: number;
  ttftMs?: number;
  status?: string;
  stopReason?: string;
  provider?: string;
  model?: string;
  toolsUsed?: string[];
  runId?: number;
  threadId?: number;
  blocks?: Record<string, string>;
  retrieval?: RetrievalTrace | null;
}

export interface ChatMessage {
  id: number;
  userId: number;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  model?: string;
  provider?: string;
  createdAt?: string;
  reasoning?: string;
  traceEvents?: TraceEvent[];
  retrieval?: RetrievalTrace | null;
  source?: string | null;
}

export interface AgentResponse {
  response: string;
  model: string;
  provider: string;
  toolsUsed: string[];
  retrieval?: RetrievalTrace | null;
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

export interface Greeting {
  message: string;
  llmGenerated: boolean;
  context: {
    currentFocus: string | null;
    openTaskCount: number;
    overdueTasks: number;
    daysSinceLastChat: number | null;
    upcomingDeadlines: string[];
  };
}

export interface SelfModelSection {
  content: string;
  version: number;
  updatedBy: string;
  updatedAt: string | null;
}

export interface PendingMemoryOpData {
  id: number;
  opType: string;
  targetBlock: string;
  content: string;
  oldContent: string | null;
  createdAt: string | null;
}

export interface SelfModelData {
  userId: number;
  sections: Record<string, SelfModelSection>;
  pendingOps: PendingMemoryOpData[];
}

export interface PendingMemoryOpsResponse {
  userId: number;
  pendingOps: PendingMemoryOpData[];
}

export interface PendingMemoryConsolidationResponse {
  userId: number;
  status: string;
  opsProcessed: number;
  opsSkipped: number;
  opsFailed: number;
  remainingPendingOps: number;
}

export interface EmotionalSignalData {
  emotion: string;
  confidence: number;
  trajectory: string;
  evidenceType: string;
  evidence: string;
  topic: string;
  createdAt: string | null;
}

export interface EmotionalContextData {
  dominantEmotion: string | null;
  recentSignals: EmotionalSignalData[];
  synthesizedContext: string;
}

export interface MemoryItemData {
  id: number;
  content: string;
  category: string;
  importance: number;
  source: string;
  isSuperseded: boolean;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface MemoryEpisodeData {
  id: number;
  date: string;
  time: string | null;
  summary: string;
  topics: string[];
  emotionalArc: string | null;
  significanceScore: number;
  turnCount: number | null;
  createdAt: string | null;
}

export interface MemorySearchResult {
  type: "item" | "episode";
  id: number;
  content: string;
  category: string;
  importance: number;
}

export interface DbTableInfo {
  name: string;
  rowCount: number;
}

export interface DbTableData {
  table: string;
  columns: string[];
  primaryKeys: string[];
  rows: Record<string, unknown>[];
  total: number;
}

export interface DbQueryResult {
  columns: string[];
  rows: Record<string, unknown>[];
  rowCount: number;
}

export interface MemoryOverviewData {
  totalItems: number;
  factCount: number;
  preferenceCount: number;
  goalCount: number;
  relationshipCount: number;
  currentFocus: string | null;
  episodeCount: number;
}

export interface GraphEntity {
  id: number;
  name: string;
  normalized: string;
  type: string;
  description: string | null;
  mentions: number;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface GraphRelationTarget {
  id: number;
  name: string;
  type: string;
}

export interface GraphRelation {
  id: number;
  type: string;
  mentions: number;
  source?: GraphRelationTarget;
  target?: GraphRelationTarget;
}

export interface GraphEntityDetail extends GraphEntity {
  outgoingRelations: GraphRelation[];
  incomingRelations: GraphRelation[];
}

export interface GraphPath {
  source: string;
  relation: string;
  destination: string;
  source_type: string;
  destination_type: string;
}

export interface GraphOverviewData {
  entityCount: number;
  relationCount: number;
  typeDistribution: Record<string, number>;
  relationTypeDistribution: Record<string, number>;
  topEntities: Array<{
    id: number;
    name: string;
    type: string;
    mentions: number;
  }>;
}

export interface GraphSearchResult {
  entities: Array<{
    id: number;
    name: string;
    type: string;
    mentions: number;
  }>;
  paths: GraphPath[];
}

export interface Thread {
  id: number;
  title: string | null;
  status: string;
  isArchived: boolean;
  lastMessageAt: string | null;
  createdAt: string | null;
}

export interface ThreadListResponse {
  threads: Thread[];
}

export interface ThreadMessage {
  role: string;
  content: string;
  ts: string | null;
  isArchivedHistory: boolean;
  retrieval?: RetrievalTrace | null;
}

export interface ThreadMessagesResponse {
  threadId: number;
  messages: ThreadMessage[];
}

export interface CreateThreadResponse {
  threadId: number;
  status: string;
  thread?: Thread;
}
