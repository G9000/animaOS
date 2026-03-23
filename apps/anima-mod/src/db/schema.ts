import { sqliteTable, text, integer, primaryKey } from "drizzle-orm/sqlite-core";
import { sql } from "drizzle-orm";

export const modConfig = sqliteTable("mod_config", {
  modId: text("mod_id").notNull(),
  key: text("key").notNull(),
  value: text("value").notNull(),
  isSecret: integer("is_secret", { mode: "boolean" }).default(false),
  updatedAt: text("updated_at").default(sql`(datetime('now'))`),
}, (table) => [
  primaryKey({ columns: [table.modId, table.key] }),
]);

export const modState = sqliteTable("mod_state", {
  modId: text("mod_id").primaryKey(),
  enabled: integer("enabled", { mode: "boolean" }).default(false),
  status: text("status").default("stopped").$type<"stopped" | "running" | "error">(),
  lastError: text("last_error"),
  startedAt: text("started_at"),
  updatedAt: text("updated_at").default(sql`(datetime('now'))`),
});

export const modEvents = sqliteTable("mod_events", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  modId: text("mod_id").notNull(),
  eventType: text("event_type").notNull().$type<"config_changed" | "started" | "stopped" | "error">(),
  detail: text("detail"),
  createdAt: text("created_at").default(sql`(datetime('now'))`),
});

export const modStore = sqliteTable("mod_store", {
  namespace: text("namespace").notNull(),
  key: text("key").notNull(),
  value: text("value").notNull(),
  updatedAt: text("updated_at").default(sql`(datetime('now'))`),
}, (table) => [
  primaryKey({ columns: [table.namespace, table.key] }),
]);
