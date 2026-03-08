// Memory context loader — builds a compact summary of what ANIMA knows
// about the user, injected into the system prompt each conversation.

import {
  listMemories,
  listSections,
  readMemory,
  type MemorySection,
} from "../memory";
import { eq } from "drizzle-orm";
import { db } from "../db";
import * as schema from "../db/schema";
import { maybeDecryptForUser } from "../lib/data-crypto";

const MAX_CONTEXT_CHARS = 3000;

interface SectionConfig {
  section: MemorySection;
  files: string[];
  label: string;
}

// Priority order: user profile data first, then relationships, then knowledge.
// Journal is excluded — it's too verbose for context injection.
const SECTIONS: SectionConfig[] = [
  { section: "user", files: ["facts", "preferences", "current-focus"], label: "About the user" },
  { section: "relationships", files: [], label: "People" },
  { section: "knowledge", files: [], label: "Knowledge" },
];

async function loadSectionContent(
  section: MemorySection,
  userId: number,
  specificFiles: string[],
): Promise<string[]> {
  const lines: string[] = [];

  if (specificFiles.length > 0) {
    // Load specific files in order
    for (const filename of specificFiles) {
      try {
        const file = await readMemory(section, userId, filename);
        const body = file.content.trim();
        if (body) {
          lines.push(body);
        }
      } catch {
        // File doesn't exist yet — skip
      }
    }
  } else {
    // Load all files in this section
    const entries = await listMemories(section, userId);
    for (const entry of entries.slice(0, 5)) {
      // Read full content for top 5 files
      const parts = entry.path.split("/");
      const filename = parts[parts.length - 1]?.replace(/\.md$/, "");
      if (!filename) continue;

      try {
        const file = await readMemory(section, userId, filename);
        const body = file.content.trim();
        if (body) {
          lines.push(body);
        }
      } catch {
        // Skip unreadable
      }
    }
  }

  return lines;
}

/**
 * Load a compact memory context string for injection into the system prompt.
 * Returns empty string if no memories exist.
 */
export async function loadMemoryContext(userId: number): Promise<string> {
  const parts: string[] = [];
  let totalChars = 0;
  const customSections = (await listSections(userId)).filter(
    (section) => !["user", "relationships", "knowledge", "journal"].includes(section),
  );
  const plan: SectionConfig[] = [
    ...SECTIONS,
    ...customSections.map((section) => ({
      section,
      files: [],
      label: `Memory: ${section}`,
    })),
  ];

  for (const { section, files, label } of plan) {
    if (totalChars >= MAX_CONTEXT_CHARS) break;

    const lines = await loadSectionContent(section, userId, files);
    if (lines.length === 0) continue;

    const sectionText = lines.join("\n");
    const remaining = MAX_CONTEXT_CHARS - totalChars;
    const trimmed =
      sectionText.length > remaining
        ? sectionText.slice(0, remaining).trimEnd()
        : sectionText;

    if (trimmed) {
      parts.push(`## ${label}\n${trimmed}`);
      totalChars += trimmed.length;
    }
  }

  // Inject tasks from DB
  try {
    const taskRows = await db
      .select()
      .from(schema.tasks)
      .where(eq(schema.tasks.userId, userId));

    if (taskRows.length > 0) {
      const openTasks = taskRows.filter((t) => !t.done);
      const doneTasks = taskRows.filter((t) => t.done);
      const taskLines: string[] = [];
      for (const t of openTasks) {
        const extra = t.dueDate ? ` (due: ${t.dueDate})` : "";
        taskLines.push(`- [ ] ${maybeDecryptForUser(userId, t.text)}${extra}`);
      }
      for (const t of doneTasks.slice(-3)) {
        taskLines.push(`- [x] ${maybeDecryptForUser(userId, t.text)}`);
      }
      if (taskLines.length > 0) {
        parts.push(`## Tasks\n${taskLines.join("\n")}`);
      }
    }
  } catch {
    // tasks table might not exist yet
  }

  if (parts.length === 0) return "";

  return `# What you know about this user\nUse this context naturally in conversation. Do not repeat it back verbatim.\n\n${parts.join("\n\n")}`;
}
