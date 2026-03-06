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

export type Message = typeof messages.$inferSelect;

export type AgentConfig = typeof agentConfig.$inferSelect;
