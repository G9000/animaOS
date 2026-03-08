// Memory consolidation — merges duplicates, prunes stale entries,
// and summarizes bloated memory files to keep context sharp.

import { SystemMessage, HumanMessage } from "@langchain/core/messages";
import { createModel } from "./models";
import type { ProviderConfig } from "../llm/types";
import {
  listMemories,
  readMemory,
  writeMemory,
  type MemorySection,
} from "../memory";
import { scheduleIndex } from "../memory/manager";
import { getAgentConfig } from "./config";

const CONSOLIDATION_PROMPT = `You are a memory consolidation system. Given a memory file's content, clean it up by:

1. Remove exact or near-duplicate entries (keep the most informative version)
2. Merge entries that describe the same thing from different angles
3. Remove entries that are too vague to be useful
4. Keep the format as markdown bullet points (- item)
5. Preserve all unique, specific information — do NOT discard real facts

Return ONLY the cleaned markdown content. No explanations, no frontmatter — just the bullet points.

If the content is already clean and has no duplicates, return it unchanged.`;

const SUMMARY_PROMPT = `You are a memory summarization system. The following memory file has grown too large. Compress it while preserving all important information.

Rules:
- Merge related items into concise statements
- Remove redundancy
- Keep specific details (names, dates, numbers, preferences)
- Output as markdown bullet points (- item)
- Aim for roughly half the original length
- Do NOT invent information — only summarize what exists

Return ONLY the cleaned markdown content.`;

// Files that should never be auto-consolidated
const SKIP_FILES = new Set(["current-focus"]);

// Max lines before a file is considered bloated and needs summarization
const BLOAT_THRESHOLD = 30;
// Min lines to bother consolidating
const MIN_LINES_TO_CONSOLIDATE = 5;

interface ConsolidationResult {
  filesProcessed: number;
  filesChanged: number;
  errors: string[];
}

function countBulletLines(content: string): number {
  return content.split("\n").filter((l) => l.trim().startsWith("- ")).length;
}

async function consolidateFile(
  section: MemorySection,
  userId: number,
  filename: string,
  config: ProviderConfig,
): Promise<boolean> {
  const file = await readMemory(section, userId, filename);
  const bulletCount = countBulletLines(file.content);

  if (bulletCount < MIN_LINES_TO_CONSOLIDATE) return false;

  const model = createModel(config);
  const isBloated = bulletCount >= BLOAT_THRESHOLD;

  const prompt = isBloated ? SUMMARY_PROMPT : CONSOLIDATION_PROMPT;

  const result = await model.invoke([
    new SystemMessage(prompt),
    new HumanMessage(file.content.trim()),
  ]);

  const newContent =
    typeof result.content === "string"
      ? result.content.trim()
      : file.content.trim();

  // Only write if the content actually changed
  if (newContent === file.content.trim()) return false;

  // Safety check: don't accept output that lost too much info
  const newBullets = countBulletLines(newContent);
  if (newBullets < bulletCount * 0.3) {
    console.warn(
      `[consolidate] Skipping ${section}/${filename}: LLM output lost too many entries (${bulletCount} → ${newBullets})`,
    );
    return false;
  }

  await writeMemory(section, userId, filename, newContent, {
    ...file.meta,
    tags: [...(file.meta.tags || []), "consolidated"],
    source: "consolidation",
  });

  // Re-index the consolidated file for vector search
  const memPath = `${section}/${filename}`;
  scheduleIndex(userId, memPath, section);

  console.log(
    `[consolidate] ${section}/${filename}: ${bulletCount} → ${newBullets} entries`,
  );
  return true;
}

/**
 * Run memory consolidation for a user.
 * Processes user/, relationships/, and knowledge/ sections.
 */
export async function consolidateMemories(
  userId: number,
): Promise<ConsolidationResult> {
  const config = await getAgentConfig(userId);
  const sectionsToProcess: MemorySection[] = [
    "user",
    "relationships",
    "knowledge",
  ];

  const result: ConsolidationResult = {
    filesProcessed: 0,
    filesChanged: 0,
    errors: [],
  };

  for (const section of sectionsToProcess) {
    const entries = await listMemories(section, userId);

    for (const entry of entries) {
      const filename = entry.path.split("/").pop()?.replace(/\.md$/, "");
      if (!filename || SKIP_FILES.has(filename)) continue;

      result.filesProcessed++;

      try {
        const changed = await consolidateFile(
          section,
          userId,
          filename,
          config,
        );
        if (changed) result.filesChanged++;
      } catch (err) {
        const msg = `${section}/${filename}: ${(err as Error).message}`;
        console.error(`[consolidate] Error: ${msg}`);
        result.errors.push(msg);
      }
    }
  }

  console.log(
    `[consolidate] Done for user ${userId}: ${result.filesChanged}/${result.filesProcessed} files changed`,
  );

  return result;
}
