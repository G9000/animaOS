// Post-conversation memory extraction.
// After each exchange, runs a lightweight LLM pass to extract
// facts, preferences, goals, and relationships — then stores them
// in the markdown memory system, deduplicating against existing entries.

import { SystemMessage, HumanMessage } from "@langchain/core/messages";
import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import {
  appendMemory,
  searchMemories,
  type MemorySection,
} from "../memory";

interface ExtractedItem {
  category: "fact" | "preference" | "goal" | "relationship";
  content: string;
}

const EXTRACTION_PROMPT = `You are a memory extraction system. Given a conversation exchange between a user and an AI companion, extract any new personal information worth remembering long-term.

Extract ONLY concrete, specific information. Do NOT extract:
- Greetings, small talk, or filler
- Things the AI said (only extract what the USER reveals)
- Vague or ambiguous statements
- Information that is only relevant to the current conversation

Categories:
- fact: Personal details (name, age, job, location, habits, experiences)
- preference: Likes, dislikes, opinions, preferred ways of doing things
- goal: Things the user wants to achieve, plans, aspirations
- relationship: People the user mentions (name + relationship/context)

Respond with a JSON array of extracted items. If nothing worth extracting, respond with an empty array [].

Format:
[{"category": "fact", "content": "works as a software engineer"}, ...]

IMPORTANT: Be selective. Only extract information that would be useful to remember in future conversations. Quality over quantity.`;

function parseExtractionResponse(text: string): ExtractedItem[] {
  // Find JSON array in the response
  const jsonMatch = text.match(/\[[\s\S]*\]/);
  if (!jsonMatch) return [];

  try {
    const parsed = JSON.parse(jsonMatch[0]);
    if (!Array.isArray(parsed)) return [];

    return parsed.filter(
      (item: any) =>
        item &&
        typeof item.category === "string" &&
        typeof item.content === "string" &&
        ["fact", "preference", "goal", "relationship"].includes(item.category) &&
        item.content.trim().length > 0,
    );
  } catch {
    return [];
  }
}

function categoryToSection(category: string): MemorySection {
  switch (category) {
    case "fact":
    case "preference":
    case "goal":
      return "user";
    case "relationship":
      return "relationships";
    default:
      return "knowledge";
  }
}

function categoryToFilename(category: string): string {
  switch (category) {
    case "fact":
      return "facts";
    case "preference":
      return "preferences";
    case "goal":
      return "goals";
    default:
      return category;
  }
}

async function isDuplicate(
  userId: number,
  content: string,
): Promise<boolean> {
  // Search existing memories for similar content
  const keywords = content
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, "")
    .split(/\s+/)
    .filter((w) => w.length > 3)
    .slice(0, 3)
    .join(" ");

  if (!keywords) return false;

  const existing = await searchMemories(userId, keywords);

  // Check if any existing memory contains substantially similar content
  const contentLower = content.toLowerCase();
  return existing.some((entry) => {
    const snippetLower = entry.snippet.toLowerCase();
    return (
      snippetLower.includes(contentLower) ||
      contentLower.includes(snippetLower.slice(0, Math.min(snippetLower.length, 60)))
    );
  });
}

/**
 * Extract and store memories from a conversation exchange.
 * Runs as fire-and-forget — errors are logged, not thrown.
 */
export async function extractMemories(
  model: BaseChatModel,
  userMessage: string,
  assistantResponse: string,
  userId: number,
): Promise<void> {
  try {
    const result = await model.invoke([
      new SystemMessage(EXTRACTION_PROMPT),
      new HumanMessage(
        `User: ${userMessage}\n\nAssistant: ${assistantResponse}`,
      ),
    ]);

    const text =
      typeof result.content === "string"
        ? result.content
        : JSON.stringify(result.content);

    const items = parseExtractionResponse(text);

    if (items.length === 0) return;

    let stored = 0;
    for (const item of items) {
      const duplicate = await isDuplicate(userId, item.content);
      if (duplicate) continue;

      const section = categoryToSection(item.category);
      const filename =
        section === "relationships"
          ? item.content
              .replace(/[^a-zA-Z0-9\s]/g, "")
              .split(/\s+/)
              .slice(0, 3)
              .join("-")
              .toLowerCase()
          : categoryToFilename(item.category);

      await appendMemory(section, userId, filename, `- ${item.content}`, {
        category: item.category,
        tags: [item.category, "auto-extracted"],
        source: "extraction",
      });

      stored++;
    }

    if (stored > 0) {
      console.log(
        `[extract] Stored ${stored} new memories for user ${userId}`,
      );
    }
  } catch (err) {
    console.error(
      "[extract] Memory extraction failed:",
      (err as Error).message,
    );
  }
}
