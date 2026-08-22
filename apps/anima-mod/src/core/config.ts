/**
 * a-mod: Configuration Loader
 * 
 * Loads anima-mod.config.yaml with environment variable substitution.
 */

import { open, readFile, rename, unlink } from "node:fs/promises";
import { dirname, join } from "node:path";
import { parse, stringify } from "yaml";
import type { ModConfig } from "./types.js";
import type { ModCredentialStore } from "../security/credential-broker.js";

export const DEFAULT_CONFIG_PATH = "./anima-mod.config.yaml";
const ENV_PLACEHOLDER = /^\$\{[A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\}$/;

interface AModConfig {
  modules?: ModConfig[];
  core?: {
    port?: number;
    hostname?: string;
    anima?: {
      baseUrl?: string;
      username?: string;
      password?: string;
      passwordRef?: string;
    };
    store?: {
      path?: string;
    };
  };
  log?: {
    level?: string;
  };
}

let cachedConfig: AModConfig | null = null;

/**
 * Load a-mod configuration from YAML
 */
export async function loadConfig(path = DEFAULT_CONFIG_PATH): Promise<AModConfig> {
  if (cachedConfig) return cachedConfig;

  try {
    const content = await readFile(path, "utf-8");
    const substituted = substituteEnv(content);
    cachedConfig = parse(substituted) as AModConfig;
    return cachedConfig;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") {
      // Return default config if file not found
      return { modules: [] };
    }
    throw err;
  }
}

async function rewriteVerified(
  path: string,
  mutate: (raw: AModConfig) => boolean,
): Promise<boolean> {
  const source = await readFile(path, "utf-8");
  const payload = parse(source) as AModConfig;
  if (!mutate(payload)) return false;
  const temporary = join(dirname(path), `.${Date.now()}-${crypto.randomUUID()}.tmp`);
  const handle = await open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(stringify(payload), "utf-8");
    await handle.sync();
    await handle.close();
    await rename(temporary, path);
  } catch (error) {
    await handle.close().catch(() => {});
    await unlink(temporary).catch(() => {});
    throw error;
  }
  const verified = parse(await readFile(path, "utf-8")) as AModConfig;
  cachedConfig = null;
  if (!verified || typeof verified !== "object") {
    throw new Error("anima-mod YAML scrub verification failed");
  }
  return true;
}

export async function scrubLiteralModSecrets(
  path: string,
  modId: string,
  secretKeys: readonly string[],
): Promise<boolean> {
  const changed = await rewriteVerified(path, (raw) => {
    const mod = raw.modules?.find((candidate) => candidate.id === modId);
    if (!mod?.config) return false;
    let changed = false;
    for (const key of secretKeys) {
      const value = mod.config[key];
      if (typeof value === "string" && value && !ENV_PLACEHOLDER.test(value)) {
        delete mod.config[key];
        changed = true;
      }
    }
    return changed;
  });
  if (!changed) return false;
  const verified = parse(await readFile(path, "utf-8")) as AModConfig;
  const config = verified.modules?.find((candidate) => candidate.id === modId)?.config;
  for (const key of secretKeys) {
    const value = config?.[key];
    if (typeof value === "string" && value && !ENV_PLACEHOLDER.test(value)) {
      throw new Error(`Literal secret '${key}' remained in anima-mod YAML`);
    }
  }
  return true;
}

export async function migrateLiteralCorePassword(
  path: string,
  credentials: ModCredentialStore,
): Promise<boolean> {
  let source: string;
  try {
    source = await readFile(path, "utf-8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw error;
  }
  const raw = parse(source) as AModConfig;
  const password = raw.core?.anima?.password;
  if (typeof password !== "string" || !password || ENV_PLACEHOLDER.test(password)) {
    return false;
  }
  const reference = credentials.reference("core-auth", "password");
  await credentials.put(reference, JSON.stringify(password));
  const verified = await credentials.resolve([reference]);
  if (verified[reference] !== JSON.stringify(password)) {
    throw new Error("Core password credential migration verification failed");
  }
  const changed = await rewriteVerified(path, (candidate) => {
    if (!candidate.core?.anima || candidate.core.anima.password !== password) {
      throw new Error("Core password changed during YAML migration");
    }
    delete candidate.core.anima.password;
    candidate.core.anima.passwordRef = reference;
    return true;
  });
  const migrated = parse(await readFile(path, "utf-8")) as AModConfig;
  if (
    migrated.core?.anima?.password !== undefined
    || migrated.core?.anima?.passwordRef !== reference
  ) {
    throw new Error("Core password remained in anima-mod YAML");
  }
  return changed;
}

/**
 * Substitute environment variables in config
 * Supports: ${VAR} or ${VAR:-default}
 */
function substituteEnv(content: string): string {
  return content.replace(/\$\{([^}]+)\}/g, (match, expr) => {
    const [varName, defaultValue] = expr.split(":-");
    const value = process.env[varName];
    if (value !== undefined) return value;
    if (defaultValue !== undefined) return defaultValue;
    return match; // Keep original if not found and no default
  });
}

/**
 * Clear config cache (useful for testing)
 */
export function clearConfigCache(): void {
  cachedConfig = null;
}
