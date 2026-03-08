import { sqliteTable, text, integer } from "drizzle-orm/sqlite-core";

export const users = sqliteTable("users", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  username: text("username").notNull().unique(),
  password: text("password").notNull(),
  name: text("name").notNull(),
  gender: text("gender"),
  age: integer("age"),
  birthday: text("birthday"),
  createdAt: text("created_at").default("CURRENT_TIMESTAMP"),
  updatedAt: text("updated_at").default("CURRENT_TIMESTAMP"),
});

export type User = typeof users.$inferSelect;
export type NewUser = typeof users.$inferInsert;

// --- Chat & Agent tables ---

export const messages = sqliteTable("messages", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  userId: integer("user_id").notNull(),
  role: text("role").notNull(), // "user" | "assistant" | "system" | "tool"
  content: text("content").notNull(),
  model: text("model"),
  provider: text("provider"),
  toolName: text("tool_name"),
  toolArgs: text("tool_args"),
  toolResult: text("tool_result"),
  createdAt: text("created_at").default("CURRENT_TIMESTAMP"),
});


export const agentConfig = sqliteTable("agent_config", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  userId: integer("user_id").notNull().unique(),
  provider: text("provider").notNull().default("ollama"), // "openrouter" | "openai" | "anthropic" | "ollama"
  model: text("model").notNull().default("qwen3:14b"),
  apiKey: text("api_key"), // encrypted, null for ollama
  ollamaUrl: text("ollama_url").default("http://localhost:11434"),
  systemPrompt: text("system_prompt"),
  createdAt: text("created_at").default("CURRENT_TIMESTAMP"),
  updatedAt: text("updated_at").default("CURRENT_TIMESTAMP"),
});

export const telegramLinks = sqliteTable("telegram_links", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  chatId: integer("chat_id").notNull().unique(),
  userId: integer("user_id").notNull().unique(),
  createdAt: text("created_at").default("CURRENT_TIMESTAMP"),
  updatedAt: text("updated_at").default("CURRENT_TIMESTAMP"),
});

export const discordLinks = sqliteTable("discord_links", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  channelId: text("channel_id").notNull().unique(),
  userId: integer("user_id").notNull().unique(),
  createdAt: text("created_at").default("CURRENT_TIMESTAMP"),
  updatedAt: text("updated_at").default("CURRENT_TIMESTAMP"),
});

export const tasks = sqliteTable("tasks", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  userId: integer("user_id").notNull(),
  text: text("text").notNull(),
  done: integer("done", { mode: "boolean" }).notNull().default(false),
  priority: integer("priority").notNull().default(0), // 0=normal, 1=high, 2=urgent
  dueDate: text("due_date"), // ISO datetime or YYYY-MM-DD
  completedAt: text("completed_at"),
  createdAt: text("created_at").default("CURRENT_TIMESTAMP"),
  updatedAt: text("updated_at").default("CURRENT_TIMESTAMP"),
});

export const agentThreads = sqliteTable("agent_threads", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  userId: integer("user_id").notNull().unique(),
  threadId: text("thread_id").notNull().unique(),
  createdAt: text("created_at").default("CURRENT_TIMESTAMP"),
  updatedAt: text("updated_at").default("CURRENT_TIMESTAMP"),
});

export const langgraphCheckpoints = sqliteTable("langgraph_checkpoints", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  threadId: text("thread_id").notNull(),
  checkpointNs: text("checkpoint_ns").notNull().default(""),
  checkpointId: text("checkpoint_id").notNull(),
  parentCheckpointId: text("parent_checkpoint_id"),
  checkpoint: text("checkpoint").notNull(),
  metadata: text("metadata").notNull(),
  createdAt: text("created_at").default("CURRENT_TIMESTAMP"),
});

export const langgraphWrites = sqliteTable("langgraph_writes", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  threadId: text("thread_id").notNull(),
  checkpointNs: text("checkpoint_ns").notNull().default(""),
  checkpointId: text("checkpoint_id").notNull(),
  taskId: text("task_id").notNull(),
  idx: integer("idx").notNull(),
  channel: text("channel").notNull(),
  value: text("value").notNull(),
  createdAt: text("created_at").default("CURRENT_TIMESTAMP"),
});

export type Message = typeof messages.$inferSelect;

export type AgentConfig = typeof agentConfig.$inferSelect;
export type TelegramLink = typeof telegramLinks.$inferSelect;
export type DiscordLink = typeof discordLinks.$inferSelect;
export type Task = typeof tasks.$inferSelect;
export type NewTask = typeof tasks.$inferInsert;
export type AgentThread = typeof agentThreads.$inferSelect;
