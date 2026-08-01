import type {
  AuthUser,
  ChangePasswordRequest as ContractChangePasswordRequest,
  ChangePasswordResponse as ContractChangePasswordResponse,
  ConfirmCorefsRecoveryCredentialRequest as ContractConfirmCorefsRecoveryCredentialRequest,
  CreateAiChatResponse as ContractCreateAiChatResponse,
  CreateAiChatRequest as ContractCreateAiChatRequest,
  LoginResponse as ContractLoginResponse,
  LoginRequest as ContractLoginRequest,
  LogoutResponse as ContractLogoutResponse,
  RegisterResponse as ContractRegisterResponse,
  RegisterRequest as ContractRegisterRequest,
  RecoverRequest as ContractRecoverRequest,
  RecoverResponse as ContractRecoverResponse,
  ConfirmRecoveryCredentialRequest as ContractConfirmRecoveryCredentialRequest,
  ConfirmRecoveryCredentialResponse as ContractConfirmRecoveryCredentialResponse,
  CorefsChangePasswordRequest as ContractCorefsChangePasswordRequest,
  CorefsCredentialResponse as ContractCorefsCredentialResponse,
  PrepareCorefsRecoveryCredentialRequest as ContractPrepareCorefsRecoveryCredentialRequest,
  PrepareCorefsRecoveryCredentialResponse as ContractPrepareCorefsRecoveryCredentialResponse,
  PrepareRecoveryCredentialRequest as ContractPrepareRecoveryCredentialRequest,
  PrepareRecoveryCredentialResponse as ContractPrepareRecoveryCredentialResponse,
  UserResponse as ContractUserResponse,
} from "@anima/auth-contracts";

export interface ApiClientOptions {
  baseUrl: string;
  getUnlockToken?: () => string | null;
  getNonce?: () => string | null;
  fetchImpl?: typeof fetch;
  credentials?: RequestCredentials;
}

export type User = AuthUser;
export interface LoginResponse extends ContractLoginResponse {}
export interface AuthResponse extends ContractRegisterResponse {}
export interface RegisterResponse extends ContractRegisterResponse {}
export interface ChangePasswordResponse extends ContractChangePasswordResponse {}
export interface LogoutResponse extends ContractLogoutResponse {}
export interface RecoverRequest extends ContractRecoverRequest {}
export interface RecoverResponse extends ContractRecoverResponse {}
export interface ChangePasswordRequest extends ContractChangePasswordRequest {}
export interface CorefsChangePasswordRequest extends ContractCorefsChangePasswordRequest {}
export interface CorefsCredentialResponse extends ContractCorefsCredentialResponse {}
export interface PrepareCorefsRecoveryCredentialRequest
  extends ContractPrepareCorefsRecoveryCredentialRequest {}
export interface PrepareCorefsRecoveryCredentialResponse
  extends ContractPrepareCorefsRecoveryCredentialResponse {}
export interface ConfirmCorefsRecoveryCredentialRequest
  extends ContractConfirmCorefsRecoveryCredentialRequest {}
export interface PrepareRecoveryCredentialRequest
  extends ContractPrepareRecoveryCredentialRequest {}
export interface PrepareRecoveryCredentialResponse
  extends ContractPrepareRecoveryCredentialResponse {}
export interface ConfirmRecoveryCredentialRequest
  extends ContractConfirmRecoveryCredentialRequest {}
export interface ConfirmRecoveryCredentialResponse
  extends ContractConfirmRecoveryCredentialResponse {}
export interface LoginRequest extends ContractLoginRequest {}
export interface RegisterRequest extends ContractRegisterRequest {}
export interface UserResponse extends ContractUserResponse {}
export interface CreateAiChatRequest extends ContractCreateAiChatRequest {}
export interface CreateAiChatResponse extends ContractCreateAiChatResponse {}

export type CoreFsPrincipalKind = "user" | "anima" | "client";

export type CoreFsOperation =
  | "stat"
  | "list"
  | "walk"
  | "glob"
  | "grep"
  | "read"
  | "search"
  | "search_readiness"
  | "mkdir"
  | "create_file"
  | "write_file"
  | "apply_patch"
  | "move"
  | "trash"
  | "restore";

export interface CoreFsOperationRequest {
  operation: CoreFsOperation;
  path?: string | null;
  root?: string | null;
  pattern?: string | null;
  query?: string | null;
  searchMode?: "exact" | "text" | "semantic";
  cursorAfter?: string | null;
  globCursorAfter?: string | null;
  grepCursorPath?: string | null;
  grepCursorByteOffset?: number | null;
  grepCursorWalkAfter?: string | null;
  cursorGeneration?: number | null;
  limit?: number;
  pageSize?: number;
  maxResults?: number;
  maxFiles?: number;
  maxMatches?: number;
  maxLineBytes?: number;
  offset?: number;
  maxBytes?: number;
  responseBytes?: number | null;
  regex?: boolean;
  includeDirectories?: boolean;
}

export interface CoreFsPrincipal {
  kind: CoreFsPrincipalKind;
  id: string;
  userId: number;
  installDigest?: string | null;
}

export interface CoreFsSelectedSnapshot {
  generation: number;
  catalogHash: string;
}

export interface CoreFsOperationResponse {
  principal: CoreFsPrincipal;
  operation: CoreFsOperation;
  selected?: CoreFsSelectedSnapshot | null;
  result?: Record<string, unknown> | null;
}

export interface CoreFSFamilyReadiness {
  total: number;
  processed: number;
  failed: number;
  degraded: boolean;
}

export interface CoreFSSecurityStatus {
  coreId: string;
  filesystemAvailable: boolean;
  readiness: {
    state: string;
    catalogGeneration: number | null;
    processedObjects: number;
    capabilities: string[];
    retryable: boolean;
    families: Record<string, CoreFSFamilyReadiness>;
  };
  rotation: {
    activeFrkVersion: number;
    pendingFrkVersion: number | null;
    decryptOnlyFrkVersions: number[];
    phase: "idle" | "prepared" | "verifying";
    passwordReopenVerified: boolean;
    recoveryReopenVerified: boolean;
    oldKeyRetirementSafe: boolean;
    oldKeyRetirementBlockers: string[];
    blindIndexGeneration: number | null;
    blindIndexPendingGeneration: number | null;
    blindIndexProgress: number;
  };
}

export interface CoreFSRotationResponse {
  success: boolean;
  unlockToken: string;
  activeFrkVersion: number;
  committedCatalogGeneration: number;
  resumed: boolean;
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
  agentBirthday?: string | null;
  thinkingMonologue: string[];
  setupComplete: boolean;
}

export interface AgentBiographyPreviewSection {
  id: string;
  title: string;
  content: string;
  source: string;
}

export interface AgentBiographyPreviewData {
  userId: number;
  agentName: string;
  relationship: string;
  agentType: string;
  avatarUrl?: string | null;
  agentBirthday?: string | null;
  birthday?: string | null;
  dominantEmotion?: string | null;
  identityDraft: string;
  personaDraft: string;
  biography: string;
  contextLine: string;
  sections: AgentBiographyPreviewSection[];
  promptBlockLabels: string[];
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

export interface ChatRequestAttachment {
  kind: "image";
  filename?: string | null;
  mimeType: string;
  data: string;
}

/**
 * A small provenance badge attached to a message — e.g. "DAILY BRIEF",
 * "CURIOUS", "LOG 56". Carried from the dashboard into a new thread and
 * persisted alongside the message in `RuntimeMessage.content_json`.
 */
export interface MessagePill {
  kind: string;
  label: string;
  ref?: string | number | null;
  url?: string | null;
  mimeType?: string | null;
  assetId?: number | null;
  messageId?: number | null;
  threadId?: number | null;
  attachmentId?: string | null;
  relatedCount?: number | null;
}

export interface ChatContextMessage {
  role: "assistant";
  content: string;
  source?: string | null;
  pills?: MessagePill[];
}

export interface TodayContext {
  date: string;
  mood?: string | null;
  energy?: string | null;
  note?: string | null;
}

export interface TodayContext {
  date: string;
  mood?: string | null;
  energy?: string | null;
  note?: string | null;
}

export interface ChatAttachment {
  id: string;
  kind: "image";
  mimeType: string;
  filename?: string | null;
  sha256?: string | null;
  sizeBytes?: number | null;
  assetId?: number | null;
  retentionState?: string | null;
  url: string;
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
  attachments?: ChatAttachment[];
  source?: string | null;
  pills?: MessagePill[];
}

export interface AgentResponse {
  response: string;
  model: string;
  provider: string;
  toolsUsed: string[];
  retrieval?: RetrievalTrace | null;
}

export interface DocumentUploadInfo {
  filename: string;
  mimeType: string;
  storagePath: string;
  sha256: string;
  sizeBytes: number;
}

export interface DocumentWorkflowCheckpoint {
  id: number;
  index: number;
  state: string;
  status: string;
  input?: Record<string, unknown> | null;
  output?: Record<string, unknown> | null;
  artifacts?: Record<string, unknown> | null;
  error?: Record<string, unknown> | null;
  createdAt?: string | null;
}

export interface DocumentWorkflow {
  id: number;
  userId: number;
  threadId?: number | null;
  workflowType: string;
  status: string;
  currentState: string;
  input?: Record<string, unknown> | null;
  result?: Record<string, unknown> | null;
  error?: Record<string, unknown> | null;
  retryCount: number;
  maxRetries: number;
  createdAt?: string | null;
  updatedAt?: string | null;
  startedAt?: string | null;
  completedAt?: string | null;
  checkpoints: DocumentWorkflowCheckpoint[];
}

export interface DocumentWorkflowActionResponse {
  workflowId: number;
  status: string;
  currentState: string;
  workflow?: DocumentWorkflow;
  document?: DocumentUploadInfo;
}

export interface ParsingPackStatus {
  state: string; // "absent" | "downloading" | "ready" | "error"
  progress: number | null;
  error: string | null;
}

export interface ReparseResult {
  status: string; // "upgraded" | "upgraded_unembedded"
  chunk_count: number;
}

export interface CapabilitiesResponse {
  parsingPack: ParsingPackStatus;
  embeddings: {
    provider: string;
    model: string;
    dim: number;
    backend: string; // "ready" | "cold" | "failed_retrying"
  };
  reranker: {
    enabled: boolean;
    model: string;
    backend: string; // "ready" | "cold" | "failed_retrying"
  };
  llm: {
    configured: boolean;
  };
  contextualChunks: boolean;
  fullDocumentContext: boolean;
}

export interface KnowledgeSource {
  id: number;
  kind: string;
  sourceUri: string;
  contentHash: string;
  title?: string | null;
  mediaType?: string | null;
  status: string;
  metadata?: Record<string, unknown> | null;
}

export interface KnowledgeSourceArtifact {
  id: number;
  artifactKind: string;
  contentHash: string;
  metadata?: Record<string, unknown> | null;
}

export interface KnowledgeSourceSpan {
  id: number;
  spanKind: string;
  locator: Record<string, unknown>;
  contentText: string;
  contentHash: string;
  metadata?: Record<string, unknown> | null;
}

export interface KnowledgeSourceResponse {
  source: KnowledgeSource;
  artifacts: KnowledgeSourceArtifact[];
  spans: KnowledgeSourceSpan[];
  compileRun?: KnowledgeBundleRun;
}

export interface KnowledgeConceptSummary {
  id: number;
  slug: string;
  title: string;
  description?: string | null;
  conceptType: string;
  status: string;
  metadata?: Record<string, unknown> | null;
}

export interface KnowledgeConceptCitation {
  id: number;
  sourceId: number;
  spanId: number;
  citationLabel?: string | null;
  quoteText?: string | null;
  sourceTitle?: string | null;
  sourceUri: string;
  spanKind: string;
  locator: Record<string, unknown>;
  contentText: string;
  metadata?: Record<string, unknown> | null;
}

export interface KnowledgeConceptLink {
  id: number;
  sourceConceptId: number;
  targetConceptId: number;
  linkType: string;
  confidence?: number | null;
  metadata?: Record<string, unknown> | null;
}

export interface KnowledgeConcept extends KnowledgeConceptSummary {
  bodyMarkdown: string;
  frontmatter: Record<string, unknown>;
  citations: KnowledgeConceptCitation[];
  links: KnowledgeConceptLink[];
}

export interface KnowledgeBundleRun {
  id: number;
  status: string;
  runType: string;
  sourceId?: number | null;
}

export interface KnowledgeLintFinding {
  code: string;
  severity: "info" | "warning" | "error" | string;
  message: string;
  conceptId?: number | null;
  sourceId?: number | null;
  linkId?: number | null;
}

export interface KnowledgeSearchEvidenceSpan {
  id: number;
  sourceId: number;
  sourceTitle?: string | null;
  sourceUri: string;
  spanKind: string;
  locator: Record<string, unknown>;
  contentText: string;
  metadata?: Record<string, unknown> | null;
}

export interface KnowledgeSearchResponse {
  query: string;
  concepts: KnowledgeConceptSummary[];
  evidenceSpans: KnowledgeSearchEvidenceSpan[];
}

export interface KnowledgeImportResponse {
  conceptCount: number;
  linkCount: number;
}

export interface ProviderInfo {
  name: string;
  defaultModel: string;
  requiresApiKey: boolean;
}

export interface OllamaModelDetails {
  format?: string | null;
  family?: string | null;
  families?: string[] | null;
  parameterSize?: string | null;
  quantizationLevel?: string | null;
}

export interface OllamaModelInfo {
  name: string;
  modifiedAt?: string | null;
  size?: number | null;
  digest?: string | null;
  details?: OllamaModelDetails | null;
}

export interface AgentConfig {
  provider: string;
  model: string;
  extractionModel?: string | null;
  ollamaUrl?: string;
  hasApiKey: boolean;
  systemPrompt?: string | null;
  // Resolved (not raw-setting) embedding provider/model — reflects the
  // bundled fastembed default when nothing has been configured.
  embeddingProvider: string;
  embeddingModel: string;
  // True when the user explicitly configured embeddings (provider set
  // directly, or via the legacy piggyback signal); false means this is
  // purely the bundled default.
  embeddingIsExplicit: boolean;
  hasEmbeddingApiKey: boolean;
}

export interface Nudge {
  type: "stale_focus" | "overdue_tasks" | "journal_gap" | "long_absence";
  message: string;
  priority: number;
}

export interface ProactiveNotice {
  id: string;
  message: string;
  source: string;
  llmGenerated: boolean;
  pills?: MessagePill[];
  context: {
    currentFocus: string | null;
    openTaskCount: number;
    overdueTasks: number;
    daysSinceLastChat: number | null;
    upcomingDeadlines: string[];
  };
  contextMessages: ChatContextMessage[];
}

export type DreamSharing = "off" | "on_ask" | "ambient";

export interface PresenceConfig {
  userId: number;
  enabled: boolean;
  mainChatEnabled: boolean;
  homeGreetingContextEnabled: boolean;
  taskNudgesEnabled: boolean;
  memoryNudgesEnabled: boolean;
  checkInNudgesEnabled: boolean;
  customInstruction?: string | null;
  initiativeEnabled: boolean;
  quietHoursStart: number | null;
  quietHoursEnd: number | null;
  dreamSharing: DreamSharing;
}

export type PresenceConfigUpdate = Partial<
  Omit<PresenceConfig, "userId">
>;

export interface PendingInitiative {
  id: number;
  drive: string;
  text: string;
  createdAt: string;
  delivered: boolean;
  acknowledged: boolean;
}

export interface PendingInitiativesResponse {
  userId: number;
  initiatives: PendingInitiative[];
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

export interface Reflection {
  question: string | null;
  llmGenerated: boolean;
  curiosityType?: "question" | "memory";
  sourceEpisodeId?: number | null;
  sourceEpisodeDate?: string | null;
}

export interface Greeting {
  message: string;
  llmGenerated: boolean;
  // IL-010: this greeting voices a consumed (surfaced) ambient dream — it is
  // one-shot: display once, never cache or replay it.
  ambientDream?: boolean;
  // The same greeting WITHOUT the dream sentence. Any surface that forwards
  // greeting text into an LLM prompt (e.g. the dashboard chat handoff) must
  // use this when present — the dream must never enter a model prompt.
  handoffMessage?: string | null;
  pills?: MessagePill[];
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
  valence: number | null;
  arousal: number | null;
}

export interface AgentStateData {
  userId: number;
  dominantEmotion: string | null;
  thought: string;
  thoughtSource: string;
  chatPrompt: string;
  contextMessages: ChatContextMessage[];
  affectHint?: string | null;
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

export interface DiaryAttachmentData {
  id: number;
  entryId: number;
  kind: "image" | "audio" | "video" | "file" | string;
  mimeType: string;
  filename: string | null;
  caption: string | null;
  sizeBytes: number;
  sha256: string;
  createdAt: string | null;
  url: string;
}

export interface DiaryEntryData {
  id: number;
  userId: number;
  entryDate: string;
  title: string | null;
  body: string;
  mood: string | null;
  source: string;
  coverAttachmentId: number | null;
  folderId: number | null;
  attachments: DiaryAttachmentData[];
  createdAt: string | null;
  updatedAt: string | null;
}

export interface DiaryEntryCreateData {
  entryDate: string;
  title?: string | null;
  body: string;
  mood?: string | null;
  folderId?: number | null;
}

export interface DiaryFolderData {
  id: number;
  userId: number;
  name: string;
  entryCount: number;
  createdAt: string | null;
}

export interface DiaryEntryUpdateData {
  entryDate?: string;
  title?: string | null;
  body?: string;
  mood?: string | null;
  coverAttachmentId?: number | null;
  folderId?: number | null;
  clearTitle?: boolean;
  clearMood?: boolean;
  clearFolder?: boolean;
  clearCover?: boolean;
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
  initiatedBy?: "user" | "agent" | null;
}

export interface ThreadListResponse {
  threads: Thread[];
}

export interface ThreadContextStats {
  threadId: number;
  usedTokens: number;
  budgetTokens: number | null;
  triggerAtTokens: number | null;
  pct: number | null;
  compactionCount: number;
  messageCount: number;
}

export interface ThreadMessage {
  id?: number | null;
  role: string;
  content: string;
  ts: string | null;
  isArchivedHistory: boolean;
  retrieval?: RetrievalTrace | null;
  attachments?: ChatAttachment[];
  pills?: MessagePill[];
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
