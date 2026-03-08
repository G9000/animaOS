// Memory route handlers

import type { Context } from "hono";
import {
  listAllMemories,
  listMemories,
  readMemory,
  writeMemory,
  appendMemory,
  deleteMemory,
  searchMemories,
  writeJournalEntry,
  type MemorySection,
} from "../../memory";
import { requireUnlockedUser } from "../../lib/require-unlock";

// GET /memory/:userId
export async function listUserMemories(c: Context) {
  const userId = parseInt(c.req.param("userId") || "");
  const section = c.req.query("section") as MemorySection | undefined;

  if (isNaN(userId)) return c.json({ error: "Invalid userId" }, 400);
  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  const entries = section
    ? await listMemories(section, userId)
    : await listAllMemories(userId);

  return c.json({ count: entries.length, memories: entries });
}

// GET /memory/:userId/search
export async function searchUserMemories(c: Context) {
  const userId = parseInt(c.req.param("userId") || "");
  const query = c.req.query("q") || "";

  if (isNaN(userId)) return c.json({ error: "Invalid userId" }, 400);
  if (!query) return c.json({ error: "Missing query param 'q'" }, 400);
  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  const results = await searchMemories(userId, query);
  return c.json({ count: results.length, results });
}

// GET /memory/:userId/:section/:filename
export async function readUserMemory(c: Context) {
  const userId = parseInt(c.req.param("userId") || "");
  const section = c.req.param("section") as MemorySection;
  const filename = c.req.param("filename") || "";

  if (isNaN(userId)) return c.json({ error: "Invalid userId" }, 400);
  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  try {
    const file = await readMemory(section, userId, filename);
    return c.json(file);
  } catch {
    return c.json({ error: "Memory file not found" }, 404);
  }
}

// PUT /memory/:userId/:section/:filename
export async function writeUserMemory(c: Context) {
  const userId = parseInt(c.req.param("userId") || "");
  const section = c.req.param("section") as MemorySection;
  const filename = c.req.param("filename") || "";

  if (isNaN(userId)) return c.json({ error: "Invalid userId" }, 400);
  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  const body = await c.req.json<{ content: string; tags?: string[] }>();
  if (!body.content) return c.json({ error: "Missing content" }, 400);

  const file = await writeMemory(section, userId, filename, body.content, {
    category: section,
    tags: body.tags || [],
    source: "user",
  });

  return c.json(file);
}

// POST /memory/:userId/:section/:filename
export async function appendUserMemory(c: Context) {
  const userId = parseInt(c.req.param("userId") || "");
  const section = c.req.param("section") as MemorySection;
  const filename = c.req.param("filename") || "";

  if (isNaN(userId)) return c.json({ error: "Invalid userId" }, 400);
  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  const body = await c.req.json<{ content: string }>();
  if (!body.content) return c.json({ error: "Missing content" }, 400);

  const file = await appendMemory(section, userId, filename, body.content, {
    category: section,
    source: "user",
  });

  return c.json(file);
}

// DELETE /memory/:userId/:section/:filename
export async function deleteUserMemory(c: Context) {
  const userId = parseInt(c.req.param("userId") || "");
  const section = c.req.param("section") as MemorySection;
  const filename = c.req.param("filename") || "";

  if (isNaN(userId)) return c.json({ error: "Invalid userId" }, 400);
  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  const deleted = await deleteMemory(section, userId, filename);
  return c.json({ deleted });
}

// POST /memory/:userId/journal
export async function writeJournal(c: Context) {
  const userId = parseInt(c.req.param("userId") || "");

  if (isNaN(userId)) return c.json({ error: "Invalid userId" }, 400);
  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  const body = await c.req.json<{ entry: string; date?: string }>();
  if (!body.entry) return c.json({ error: "Missing entry" }, 400);

  const file = await writeJournalEntry(userId, body.entry, body.date);
  return c.json(file);
}
