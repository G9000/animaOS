import type { ProviderConfig } from "../llm/types";
import { buildAgent } from "./builder";
import { invalidateSoulPromptCache } from "./prompt";

// ── Agent graph cache ────────────────────────────────────────────
// Avoids rebuilding the graph on every request. Keyed by a hash of
// the values that actually influence graph shape: provider config + prompt.

interface CachedAgent {
  agent: ReturnType<typeof buildAgent>;
  key: string;
}

const agentCache = new Map<number, CachedAgent>();

function agentCacheKey(
  config: ProviderConfig,
  fullSystemPrompt: string,
): string {
  return `${config.provider}:${config.model}:${config.apiKey ?? ""}:${config.ollamaUrl ?? ""}:${fullSystemPrompt.length}`;
}

/**
 * Return a cached agent graph, or build + cache a new one when the
 * underlying config / prompt has changed.
 */
export function getOrBuildAgent(
  config: ProviderConfig,
  userId: number,
  fullSystemPrompt: string,
) {
  const key = agentCacheKey(config, fullSystemPrompt);
  const cached = agentCache.get(userId);
  if (cached && cached.key === key) return cached.agent;

  const agent = buildAgent(config, userId, fullSystemPrompt);
  agentCache.set(userId, { agent, key });
  return agent;
}

/** Force re-read of prompt files (soul.md / factory.md) and clear cached agents. */
export function invalidateSoulCache(): void {
  invalidateSoulPromptCache();
  agentCache.clear();
}
