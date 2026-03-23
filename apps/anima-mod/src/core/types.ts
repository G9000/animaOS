/**
 * a-mod: Core Types
 * 
 * The module contract. All a-mod modules implement this interface.
 * Pure foundation - no channel-specific types here.
 */

import type { AnyElysia } from "elysia";

/** Module context - services provided to all modules */
export interface ModContext {
  /** Module ID */
  readonly modId: string;

  /** Module configuration from anima-mod.config.yaml */
  config: Record<string, unknown>;

  /** Logger instance scoped to this module */
  logger: Logger;

  /** HTTP client to cognitive core (apps/server) */
  anima: AnimaClient;

  /** Module-private key-value store (SQLite-backed) */
  store: ModStore;

  /** Message dispatch bus for cross-module communication */
  dispatch: DispatchBus;
}

/** Module interface - all a-mod modules implement this */
export interface Mod {
  /** Unique module identifier (kebab-case recommended) */
  id: string;

  /** Semantic version */
  version: string;

  /** 
   * Initialize the module.
   * Called before start(). Set up internal state here.
   */
  init(ctx: ModContext): Promise<void>;

  /**
   * Start the module.
   * Called after all modules are initialized.
   * Begin operations (connect websockets, start polling, etc.)
   */
  start(): Promise<void>;

  /**
   * Graceful shutdown.
   * Called during shutdown. Clean up resources here.
   */
  stop?(): Promise<void>;

  /**
   * Get Elysia router for this module.
   * Routes will be mounted at /{modId}/*
   */
  getRouter?(): AnyElysia;

  /** Config schema for schema-driven UI. Desktop renders settings forms from this. */
  configSchema?: ModConfigSchema;

  /** Optional setup wizard steps. If present, desktop shows wizard for first-time setup. */
  setupGuide?: SetupStep[];
}

/** Logger interface */
export interface Logger {
  debug(msg: string, meta?: Record<string, unknown>): void;
  info(msg: string, meta?: Record<string, unknown>): void;
  warn(msg: string, meta?: Record<string, unknown>): void;
  error(msg: string, meta?: Record<string, unknown>): void;
}

/** Anima cognitive core client */
export interface AnimaClient {
  /** Send chat message to cognitive core */
  chat(req: ChatRequest): Promise<ChatResponse>;

  /** Link a channel chat to anima user */
  linkChannel(channel: string, chatId: string, userId: number, secret?: string): Promise<void>;

  /** Unlink channel chat */
  unlinkChannel(channel: string, chatId: string): Promise<void>;

  /** Lookup anima user by channel chat */
  lookupUser(channel: string, chatId: string): Promise<number | null>;
}

/** Chat request to cognitive core */
export interface ChatRequest {
  userId: number;
  message: string;
  context?: {
    source: string;
    chatId: string;
    taskId?: string;
    [key: string]: unknown;
  };
  stream?: boolean;
}

/** Chat response from cognitive core */
export interface ChatResponse {
  response: string;
  model: string;
  provider: string;
  toolsUsed: string[];
}

/** Module-private KV store */
export interface ModStore {
  /** Get value by key */
  get<T>(key: string): Promise<T | null>;
  
  /** Set value */
  set<T>(key: string, value: T): Promise<void>;
  
  /** Delete key */
  delete(key: string): Promise<void>;
  
  /** Check if key exists */
  has(key: string): Promise<boolean>;
}

/** Dispatch bus for cross-module communication */
export interface DispatchBus {
  /** 
   * Send message to specific user across all their linked channels.
   * a-mod routes to appropriate channel based on user preference/availability.
   */
  sendToUser(userId: number, message: string, options?: SendOptions): Promise<void>;

  /**
   * Send message to specific channel/chat.
   */
  sendToChannel(channel: string, chatId: string, message: string): Promise<void>;

  /**
   * Subscribe to messages from a specific source.
   * Used by modules to receive relevant messages.
   */
  onMessage(handler: MessageHandler): () => void;

  /**
   * Create a task for the agent.
   */
  createTask(task: Omit<Task, "id">): Promise<string>;

  /**
   * Subscribe to task assignments.
   */
  onTask(handler: TaskHandler): () => void;
}

/** Send options */
export interface SendOptions {
  /** Prefer specific channel if available */
  preferChannel?: string;
  /** High priority - bypass quiet hours, etc. */
  priority?: "low" | "normal" | "high";
}

/** Message handler */
export type MessageHandler = (msg: InboundMessage) => void | Promise<void>;

/** Inbound message */
export interface InboundMessage {
  id: string;
  source: string;      // channel type: "telegram", "discord", etc.
  chatId: string;      // channel-specific chat ID
  userId?: number;     // anima user ID (if linked)
  text: string;
  timestamp: Date;
  raw: unknown;        // original platform payload
}

/** Task handler */
export type TaskHandler = (task: Task) => void | Promise<void>;

/** Task definition */
export interface Task {
  id: string;
  type: string;
  title: string;
  description?: string;
  assignee?: number;   // anima user ID
  priority: "low" | "normal" | "high" | "urgent";
  status: "pending" | "in_progress" | "done";
  dueDate?: Date;
  metadata?: Record<string, unknown>;
}

/** Module manifest (loaded from mod's package.json or mod.json) */
export interface ModManifest {
  id: string;
  version: string;
  description?: string;
  dependencies?: string[];  // other mod IDs this mod depends on
}

/** Module config from a-mod.config.yaml */
export interface ModConfig {
  id: string;
  path: string;
  config: Record<string, unknown>;
}

/** Config field types for schema-driven UI */
export type FieldType = "string" | "number" | "boolean" | "enum" | "secret";

/** Single config field definition */
export interface ConfigField {
  type: FieldType;
  label: string;
  required?: boolean;
  default?: unknown;
  options?: string[];
  showWhen?: Record<string, unknown>;
  description?: string;
}

/** Schema for mod configuration — desktop renders forms from this */
export type ModConfigSchema = Record<string, ConfigField>;

/** Setup wizard step */
export interface SetupStep {
  step: number;
  title: string;
  instructions?: string;
  field?: string;
  action?: "healthcheck";
}
