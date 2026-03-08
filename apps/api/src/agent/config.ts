import { eq } from "drizzle-orm";
import { db } from "../db";
import * as schema from "../db/schema";
import type { ProviderConfig } from "../llm/types";
import { maybeDecryptForUser } from "../lib/data-crypto";

/**
 * Load the user's agent config from the DB, falling back to local Ollama
 * with qwen3:14b when no row exists.
 */
export async function getAgentConfig(
  userId: number,
): Promise<ProviderConfig & { systemPrompt?: string }> {
  const [cfg] = await db
    .select()
    .from(schema.agentConfig)
    .where(eq(schema.agentConfig.userId, userId));

  if (cfg) {
    return {
      provider: cfg.provider as any,
      model: cfg.model,
      apiKey: cfg.apiKey ? maybeDecryptForUser(userId, cfg.apiKey) : undefined,
      ollamaUrl: cfg.ollamaUrl || undefined,
      systemPrompt: cfg.systemPrompt
        ? maybeDecryptForUser(userId, cfg.systemPrompt)
        : undefined,
    };
  }

  return {
    provider: "ollama",
    model: "qwen3:14b",
    ollamaUrl: "http://localhost:11434",
  };
}
