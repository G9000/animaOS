import { mkdirSync } from "node:fs";
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
export const USER_DATA_ROOT = join(DATA_DIR, "users");

export function getUserDataDir(userId: number): string {
  return join(USER_DATA_ROOT, String(userId));
}

export function getUserMemoryDir(userId: number): string {
  return join(getUserDataDir(userId), "memory");
}

export function getUserSoulPath(userId: number): string {
  return join(getUserDataDir(userId), "soul.md");
}

export function ensureRuntimeLayoutSync(): void {
  mkdirSync(DATA_DIR, { recursive: true });
  mkdirSync(USER_DATA_ROOT, { recursive: true });
}

export async function ensureRuntimeLayout(): Promise<void> {
  ensureRuntimeLayoutSync();
}
