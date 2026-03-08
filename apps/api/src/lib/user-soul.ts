import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import {
  PROMPTS_DIR,
  getUserDataDir,
  getUserSoulPath as getUserScopedSoulPath,
} from "./runtime-paths";
import {
  decryptTextWithDek,
  encryptTextWithDek,
  requireDekForUser,
} from "./data-crypto";

const DEFAULT_SOUL_CONTENT = "# ANIMA Soul\n";
const SOUL_TEMPLATE_DIR = join(PROMPTS_DIR, "soul-templates");

export type SoulTemplateId = "default" | "alice";

function getUserSoulPath(userId: number): string {
  return getUserScopedSoulPath(userId);
}

async function loadSoulTemplateContent(
  templateId: SoulTemplateId = "default",
): Promise<string> {
  const path = join(SOUL_TEMPLATE_DIR, `${templateId}.md`);
  if (!existsSync(path)) return DEFAULT_SOUL_CONTENT;

  const content = (await readFile(path, "utf-8")).trim();
  return content || DEFAULT_SOUL_CONTENT;
}

export async function readUserSoul(userId: number): Promise<{ path: string; content: string }> {
  const path = getUserSoulPath(userId);

  if (!existsSync(path)) {
    const defaultContent = await loadSoulTemplateContent("default");
    await writeUserSoul(userId, defaultContent);
    return { path, content: defaultContent };
  }

  const raw = await readFile(path, "utf-8");
  const dek = requireDekForUser(userId);
  const content = decryptTextWithDek(raw, dek);
  return { path, content };
}

export async function writeUserSoul(userId: number, content: string): Promise<{ path: string }> {
  await mkdir(getUserDataDir(userId), { recursive: true });
  const path = getUserSoulPath(userId);
  const dek = requireDekForUser(userId);
  const encrypted = encryptTextWithDek(content, dek);
  await writeFile(path, encrypted, "utf-8");
  return { path };
}

export async function ensureDefaultSoul(
  userId: number,
  templateId: SoulTemplateId = "default",
): Promise<void> {
  const path = getUserSoulPath(userId);
  if (existsSync(path)) return;

  const content = await loadSoulTemplateContent(templateId);
  await writeUserSoul(userId, content);
}
