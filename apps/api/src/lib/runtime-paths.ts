import { existsSync, mkdirSync } from "node:fs";
import { join, resolve } from "node:path";

function requireEnv(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

export const DATA_DIR = resolve(requireEnv("ANIMA_DATA_DIR"));
export const PROMPTS_DIR = resolve(requireEnv("ANIMA_PROMPTS_DIR"));
export const MIGRATIONS_DIR = resolve(requireEnv("ANIMA_MIGRATIONS_DIR"));

export const DB_PATH = join(DATA_DIR, "anima.db");
export const MEMORY_ROOT = join(DATA_DIR, "memory");
export const SOUL_DIR = join(DATA_DIR, "soul");
export const SOUL_PATH = join(SOUL_DIR, "soul.md");
export const DEFAULT_SOUL_PATH = process.env.ANIMA_DEFAULT_SOUL_PATH
  ? resolve(process.env.ANIMA_DEFAULT_SOUL_PATH)
  : null;

export function ensureRuntimeLayoutSync(): void {
  mkdirSync(DATA_DIR, { recursive: true });
  mkdirSync(MEMORY_ROOT, { recursive: true });
  mkdirSync(SOUL_DIR, { recursive: true });
}

export async function ensureRuntimeLayout(): Promise<void> {
  ensureRuntimeLayoutSync();
}
