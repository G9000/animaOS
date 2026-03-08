// Memory filesystem — reads/writes markdown files with YAML frontmatter
// as ANIMA's long-term memory store.
//
// Memory lives under ANIMA_DATA_DIR: /memory/{section}/{userId}/{file}.md
// This module handles all CRUD operations on the filesystem.

import { readdir, readFile, writeFile, mkdir, unlink, stat } from "fs/promises";
import { join, basename, relative } from "path";
import { existsSync } from "fs";
import { MEMORY_ROOT } from "../lib/runtime-paths";
import {
  decryptTextWithDek,
  encryptTextWithDek,
  requireDekForUser,
} from "../lib/data-crypto";

// --- Types ---

export interface MemoryFrontmatter {
  category: string;
  tags: string[];
  created: string;
  updated: string;
  source: string;
  [key: string]: unknown;
}

export interface MemoryFile {
  /** Relative path from memory root, e.g. "user/1/preferences.md" */
  path: string;
  /** Parsed frontmatter */
  meta: MemoryFrontmatter;
  /** Markdown body (without frontmatter) */
  content: string;
}

export interface MemoryEntry {
  path: string;
  meta: MemoryFrontmatter;
  /** First 200 chars of body for search results */
  snippet: string;
}

// --- Constants ---

const SECTIONS = ["user", "knowledge", "relationships", "journal"] as const;
export type MemorySection = string;

// --- Frontmatter Parser/Serializer ---
// Lightweight — no dependency needed. Handles simple YAML frontmatter.

function parseFrontmatter(raw: string): {
  meta: Record<string, unknown>;
  content: string;
} {
  const fmRegex = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/;
  const match = raw.match(fmRegex);

  if (!match) {
    return { meta: {}, content: raw };
  }

  const yamlBlock = match[1];
  const content = match[2];

  // Simple YAML parser for flat key-value + arrays
  const meta: Record<string, unknown> = {};
  for (const line of yamlBlock.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;

    const colonIdx = trimmed.indexOf(":");
    if (colonIdx === -1) continue;

    const key = trimmed.slice(0, colonIdx).trim();
    let value: unknown = trimmed.slice(colonIdx + 1).trim();

    // Handle inline arrays: [tag1, tag2]
    if (
      typeof value === "string" &&
      value.startsWith("[") &&
      value.endsWith("]")
    ) {
      value = value
        .slice(1, -1)
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    }

    meta[key] = value;
  }

  return { meta, content };
}

function serializeFrontmatter(
  meta: Record<string, unknown>,
  content: string,
): string {
  const lines: string[] = ["---"];

  for (const [key, value] of Object.entries(meta)) {
    if (Array.isArray(value)) {
      lines.push(`${key}: [${value.join(", ")}]`);
    } else {
      lines.push(`${key}: ${value}`);
    }
  }

  lines.push("---");
  lines.push("");
  lines.push(content);

  return lines.join("\n");
}

// --- Core API ---

/** Ensure the directory for a user's memory section exists */
async function ensureDir(
  section: MemorySection,
  userId: number,
): Promise<string> {
  const dir = join(MEMORY_ROOT, section, String(userId));
  await mkdir(dir, { recursive: true });
  return dir;
}

/** Retry a function with exponential backoff */
async function retryWithBackoff<T>(
  fn: () => Promise<T>,
  operation: string,
  maxRetries = 3,
): Promise<T> {
  let lastError: Error | null = null;

  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err as Error;
      console.error(
        `[memory] ${operation} failed (attempt ${attempt + 1}/${maxRetries}):`,
        lastError.message,
      );

      if (attempt < maxRetries - 1) {
        // Exponential backoff: 100ms, 200ms, 400ms
        const delay = 100 * Math.pow(2, attempt);
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }
  }

  throw new Error(
    `${operation} failed after ${maxRetries} attempts: ${lastError?.message}`,
  );
}

/** Validate that a file was successfully written */
async function validateFileWritten(
  filePath: string,
  expectedContent: string,
): Promise<boolean> {
  try {
    // Check file exists
    if (!existsSync(filePath)) {
      console.error(
        `[memory] Validation failed: file does not exist at ${filePath}`,
      );
      return false;
    }

    // Check file is readable and has content
    const stats = await stat(filePath);
    if (stats.size === 0) {
      console.error(`[memory] Validation failed: file is empty at ${filePath}`);
      return false;
    }

    // Verify content matches
    const actualContent = await readFile(filePath, "utf-8");
    if (actualContent !== expectedContent) {
      console.error(
        `[memory] Validation failed: content mismatch at ${filePath}`,
      );
      return false;
    }

    return true;
  } catch (err) {
    console.error(
      `[memory] Validation error for ${filePath}:`,
      (err as Error).message,
    );
    return false;
  }
}

/** Get the full path for a memory file */
function memoryPath(
  section: MemorySection,
  userId: number,
  filename: string,
): string {
  // Sanitize filename
  const safe = filename.replace(/[^a-zA-Z0-9_\-\.]/g, "_");
  const name = safe.endsWith(".md") ? safe : `${safe}.md`;
  return join(MEMORY_ROOT, section, String(userId), name);
}

/** Relative path from memory root */
function relPath(fullPath: string): string {
  return relative(MEMORY_ROOT, fullPath).replace(/\\/g, "/");
}

function encryptMemoryRaw(userId: number, raw: string): string {
  const dek = requireDekForUser(userId);
  return encryptTextWithDek(raw, dek);
}

function decryptMemoryRaw(userId: number, raw: string): string {
  // Backward compatibility for any pre-encryption plaintext files.
  if (!raw.startsWith("enc1:")) return raw;
  const dek = requireDekForUser(userId);
  return decryptTextWithDek(raw, dek);
}

async function migratePlaintextMemoryFileIfNeeded(
  filePath: string,
  userId: number,
  raw: string,
): Promise<void> {
  if (raw.startsWith("enc1:")) return;
  const encrypted = encryptMemoryRaw(userId, raw);
  await writeFile(filePath, encrypted, "utf-8");
}

function parseUserIdFromRelativePath(path: string): number {
  const parts = path.split("/").filter(Boolean);
  if (parts.length < 3) {
    throw new Error(`Invalid memory path: ${path}`);
  }
  const userId = Number(parts[1]);
  if (!Number.isFinite(userId)) {
    throw new Error(`Invalid userId in memory path: ${path}`);
  }
  return userId;
}

/**
 * List known sections for a user.
 * Includes default sections plus any custom top-level folders under /memory
 * that contain the user's directory.
 */
export async function listSections(userId: number): Promise<MemorySection[]> {
  const merged = new Set<string>(SECTIONS);

  if (!existsSync(MEMORY_ROOT)) {
    return Array.from(merged);
  }

  const topLevel = await readdir(MEMORY_ROOT, { withFileTypes: true });
  for (const entry of topLevel) {
    if (!entry.isDirectory()) continue;
    if (entry.name.startsWith(".")) continue;

    const userDir = join(MEMORY_ROOT, entry.name, String(userId));
    if (existsSync(userDir)) {
      merged.add(entry.name);
    }
  }

  return Array.from(merged);
}

/**
 * Write a memory file. Creates or overwrites.
 * Includes retry logic and validation to ensure file is written successfully.
 */
export async function writeMemory(
  section: MemorySection,
  userId: number,
  filename: string,
  content: string,
  meta: Partial<MemoryFrontmatter> = {},
): Promise<MemoryFile> {
  const filePath = await retryWithBackoff(async () => {
    await ensureDir(section, userId);
    return memoryPath(section, userId, filename);
  }, `ensureDir(${section}/${userId})`);

  const now = new Date().toISOString();
  const existing = await readMemory(section, userId, filename).catch(
    () => null,
  );

  const fullMeta: MemoryFrontmatter = {
    category: meta.category || section,
    tags: (meta.tags as string[]) || [],
    created: existing?.meta.created || meta.created || now,
    updated: now,
    source: meta.source || "conversation",
    ...meta,
  };

  const raw = serializeFrontmatter(fullMeta, content);
  const encryptedRaw = encryptMemoryRaw(userId, raw);

  // Write with retry and validation
  await retryWithBackoff(
    async () => {
      await writeFile(filePath, encryptedRaw, "utf-8");

      // Validate the write succeeded
      const isValid = await validateFileWritten(filePath, encryptedRaw);
      if (!isValid) {
        throw new Error(`File validation failed for ${filePath}`);
      }

      console.log(
        `[memory] Successfully wrote and validated: ${relPath(filePath)}`,
      );
    },
    `writeMemory(${relPath(filePath)})`,
  );

  return { path: relPath(filePath), meta: fullMeta, content };
}

/**
 * Append content to an existing memory file, or create it if it doesn't exist.
 * Uses the same retry and validation logic as writeMemory.
 */
export async function appendMemory(
  section: MemorySection,
  userId: number,
  filename: string,
  newContent: string,
  meta: Partial<MemoryFrontmatter> = {},
): Promise<MemoryFile> {
  const existing = await readMemory(section, userId, filename).catch(
    () => null,
  );

  if (existing) {
    console.log(
      `[memory] Appending to existing file: ${section}/${userId}/${filename}`,
    );
    const combined = existing.content.trimEnd() + "\n" + newContent;
    return writeMemory(section, userId, filename, combined, {
      ...existing.meta,
      ...meta,
      created: existing.meta.created, // preserve original creation date
    });
  }

  console.log(`[memory] Creating new file: ${section}/${userId}/${filename}`);
  return writeMemory(section, userId, filename, newContent, meta);
}

/**
 * Read a single memory file.
 */
export async function readMemory(
  section: MemorySection,
  userId: number,
  filename: string,
): Promise<MemoryFile> {
  const filePath = memoryPath(section, userId, filename);
  const encryptedRaw = await readFile(filePath, "utf-8");
  await migratePlaintextMemoryFileIfNeeded(filePath, userId, encryptedRaw);
  const raw = decryptMemoryRaw(userId, encryptedRaw);
  const { meta, content } = parseFrontmatter(raw);

  return {
    path: relPath(filePath),
    meta: meta as MemoryFrontmatter,
    content,
  };
}

/**
 * Delete a memory file.
 */
export async function deleteMemory(
  section: MemorySection,
  userId: number,
  filename: string,
): Promise<boolean> {
  const filePath = memoryPath(section, userId, filename);
  try {
    await unlink(filePath);
    return true;
  } catch {
    return false;
  }
}

/**
 * List all memory files for a user in a given section.
 */
export async function listMemories(
  section: MemorySection,
  userId: number,
): Promise<MemoryEntry[]> {
  const dir = join(MEMORY_ROOT, section, String(userId));
  if (!existsSync(dir)) return [];

  const files = await readdir(dir);
  const entries: MemoryEntry[] = [];

  for (const file of files) {
    if (!file.endsWith(".md")) continue;

    try {
      const filePath = join(dir, file);
      const encryptedRaw = await readFile(filePath, "utf-8");
      await migratePlaintextMemoryFileIfNeeded(filePath, userId, encryptedRaw);
      const raw = decryptMemoryRaw(userId, encryptedRaw);
      const { meta, content } = parseFrontmatter(raw);

      entries.push({
        path: relPath(filePath),
        meta: meta as MemoryFrontmatter,
        snippet: content.trim().slice(0, 200),
      });
    } catch {
      // Skip unreadable files
    }
  }

  return entries;
}

/**
 * List all memory files across ALL sections for a user.
 */
export async function listAllMemories(userId: number): Promise<MemoryEntry[]> {
  const all: MemoryEntry[] = [];
  const sections = await listSections(userId);
  for (const section of sections) {
    const entries = await listMemories(section, userId);
    all.push(...entries);
  }
  return all;
}

/**
 * Search memory files by keyword across all sections for a user.
 * Searches both content and tags.
 */
export async function searchMemories(
  userId: number,
  query: string,
): Promise<MemoryEntry[]> {
  const all = await listAllMemories(userId);
  const queryLower = query.toLowerCase();
  const queryTerms = queryLower.split(/\s+/).filter(Boolean);

  return all
    .map((entry) => {
      const searchText = [
        entry.snippet,
        entry.path,
        ...(Array.isArray(entry.meta.tags) ? entry.meta.tags : []),
        entry.meta.category || "",
      ]
        .join(" ")
        .toLowerCase();

      // Score: how many query terms appear in the text
      const score = queryTerms.filter((term) =>
        searchText.includes(term),
      ).length;
      return { ...entry, score };
    })
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score)
    .map(({ score, ...entry }) => entry);
}

/**
 * Read ALL memory content for a user (for full context loading).
 * Returns concatenated markdown, useful for stuffing into system prompt.
 */
export async function loadFullMemoryContext(userId: number): Promise<string> {
  const entries = await listAllMemories(userId);
  if (entries.length === 0) return "";

  const parts: string[] = ["# Memory Context\n"];

  for (const entry of entries) {
    try {
      // Determine section from path
      const section = entry.path.split("/")[0];
      const filename = basename(entry.path, ".md");

      // Read full content
      const file = await readMemoryByPath(entry.path);
      parts.push(`## [${section}/${filename}]\n`);
      parts.push(file.content.trim());
      parts.push("");
    } catch {
      // Skip unreadable
    }
  }

  return parts.join("\n");
}

/**
 * Read a memory file by its relative path (from memory root).
 */
export async function readMemoryByPath(
  relativePath: string,
): Promise<MemoryFile> {
  const filePath = join(MEMORY_ROOT, relativePath);
  const encryptedRaw = await readFile(filePath, "utf-8");
  const userId = parseUserIdFromRelativePath(relativePath);
  await migratePlaintextMemoryFileIfNeeded(filePath, userId, encryptedRaw);
  const raw = decryptMemoryRaw(userId, encryptedRaw);
  const { meta, content } = parseFrontmatter(raw);

  return {
    path: relativePath,
    meta: meta as MemoryFrontmatter,
    content,
  };
}

/**
 * Write a journal entry for today (or a specific date).
 */
export async function writeJournalEntry(
  userId: number,
  content: string,
  date?: string,
): Promise<MemoryFile> {
  const dateStr = date || new Date().toISOString().slice(0, 10); // YYYY-MM-DD

  return appendMemory("journal", userId, dateStr, content, {
    category: "journal",
    tags: ["daily", "session"],
    source: "agent",
  });
}

// --- Export memory root for reference ---
export { MEMORY_ROOT, SECTIONS };
