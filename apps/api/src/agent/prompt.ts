import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { DEFAULT_SOUL_PATH, PROMPTS_DIR, SOUL_PATH } from "../lib/runtime-paths";
import { readUserSoul } from "../lib/user-soul";

function readPromptFile(path: string): string | null {
  try {
    const prompt = readFileSync(path, "utf8").trim();
    return prompt || null;
  } catch {
    return null;
  }
}

function resolvePromptTemplatePath(name: string): string[] {
  const filename = name.endsWith(".md") || name.endsWith(".txt")
    ? name
    : `${name}.md`;

  return [resolve(PROMPTS_DIR, filename)];
}

function loadSoulPrompt(): string {
  const soulCandidates = DEFAULT_SOUL_PATH ? [SOUL_PATH, DEFAULT_SOUL_PATH] : [SOUL_PATH];
  for (const path of soulCandidates) {
    const prompt = readPromptFile(path);
    if (prompt) return prompt;
  }

  throw new Error(
    `No soul prompt found. Checked: ${soulCandidates.join(", ")}`,
  );
}

let cachedSoulPrompt: string | null = null;
const cachedSoulPromptByUser = new Map<number, string>();
const promptTemplateCache = new Map<string, string>();

export function getSoulPrompt(): string {
  if (!cachedSoulPrompt) {
    cachedSoulPrompt = loadSoulPrompt();
  }
  return cachedSoulPrompt;
}

export async function getSoulPromptForUser(userId: number): Promise<string> {
  const cached = cachedSoulPromptByUser.get(userId);
  if (cached) return cached;

  try {
    const { content } = await readUserSoul(userId);
    const prompt = content.trim();
    if (prompt) {
      cachedSoulPromptByUser.set(userId, prompt);
      return prompt;
    }
  } catch {
    // Fall through to global prompt fallback.
  }

  const fallback = getSoulPrompt();
  cachedSoulPromptByUser.set(userId, fallback);
  return fallback;
}

export function getPromptTemplate(name: string): string {
  const cached = promptTemplateCache.get(name);
  if (cached) return cached;

  const candidates = resolvePromptTemplatePath(name);
  for (const path of candidates) {
    const prompt = readPromptFile(path);
    if (prompt) {
      promptTemplateCache.set(name, prompt);
      return prompt;
    }
  }

  throw new Error(
    `No prompt template found for "${name}". Checked: ${candidates.join(", ")}`,
  );
}

export function renderPromptTemplate(
  name: string,
  variables: Record<string, string>,
): string {
  const template = getPromptTemplate(name);
  return template.replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (_, key: string) =>
    variables[key] ?? "",
  );
}

export function invalidateSoulPromptCache(): void {
  cachedSoulPrompt = null;
  cachedSoulPromptByUser.clear();
  promptTemplateCache.clear();
}
