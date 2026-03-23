import { Database } from "bun:sqlite";
import { drizzle } from "drizzle-orm/bun-sqlite";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import * as schema from "./schema.js";

let db: ReturnType<typeof drizzle> | null = null;
let sqlite: Database | null = null;

const CREATE_TABLES_SQL = `
  CREATE TABLE IF NOT EXISTS mod_config (
    mod_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    is_secret INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (mod_id, key)
  );
  CREATE TABLE IF NOT EXISTS mod_state (
    mod_id TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 0,
    status TEXT DEFAULT 'stopped',
    last_error TEXT,
    started_at TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
  );
  CREATE TABLE IF NOT EXISTS mod_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mod_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT,
    created_at TEXT DEFAULT (datetime('now'))
  );
  CREATE TABLE IF NOT EXISTS mod_store (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (namespace, key)
  );
`;

export function getDb(dbPath = "./data/a-mod.db"): ReturnType<typeof drizzle> {
  if (db) return db;

  mkdirSync(dirname(dbPath), { recursive: true });
  sqlite = new Database(dbPath);
  sqlite.exec("PRAGMA journal_mode = WAL;");
  sqlite.exec(CREATE_TABLES_SQL);
  db = drizzle(sqlite, { schema });
  return db;
}

export function closeDb(): void {
  sqlite?.close();
  sqlite = null;
  db = null;
}

export { schema };
