import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { DEFAULT_SOUL_PATH, SOUL_DIR } from "./runtime-paths";
import {
  decryptTextWithDek,
  encryptTextWithDek,
  requireDekForUser,
} from "./data-crypto";

const DEFAULT_SOUL_CONTENT = "# ANIMA Soul\n";

function getUserSoulPath(userId: number): string {
  return join(SOUL_DIR, `${userId}.soul.md`);
}

async function loadDefaultSoulContent(): Promise<string> {
  if (DEFAULT_SOUL_PATH && existsSync(DEFAULT_SOUL_PATH)) {
    return readFile(DEFAULT_SOUL_PATH, "utf-8");
  }
  return DEFAULT_SOUL_CONTENT;
}

async function migratePlaintextSoulIfNeeded(
  userId: number,
  path: string,
  raw: string,
): Promise<void> {
  if (raw.startsWith("enc1:")) return;
  const dek = requireDekForUser(userId);
  const encrypted = encryptTextWithDek(raw, dek);
  await writeFile(path, encrypted, "utf-8");
}

export async function readUserSoul(userId: number): Promise<{ path: string; content: string }> {
  const path = getUserSoulPath(userId);

  if (!existsSync(path)) {
    const defaultContent = await loadDefaultSoulContent();
    await writeUserSoul(userId, defaultContent);
    return { path, content: defaultContent };
  }

  const raw = await readFile(path, "utf-8");
  await migratePlaintextSoulIfNeeded(userId, path, raw);

  if (!raw.startsWith("enc1:")) {
    return { path, content: raw };
  }

  const dek = requireDekForUser(userId);
  const content = decryptTextWithDek(raw, dek);
  return { path, content };
}

export async function writeUserSoul(userId: number, content: string): Promise<{ path: string }> {
  await mkdir(SOUL_DIR, { recursive: true });
  const path = getUserSoulPath(userId);
  const dek = requireDekForUser(userId);
  const encrypted = encryptTextWithDek(content, dek);
  await writeFile(path, encrypted, "utf-8");
  return { path };
}

export async function ensureDefaultSoul(userId: number): Promise<void> {
  const path = getUserSoulPath(userId);
  if (existsSync(path)) return;

  const content = await loadDefaultSoulContent();
  await writeUserSoul(userId, content);
}
