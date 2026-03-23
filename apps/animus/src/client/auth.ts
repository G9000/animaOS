import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { homedir } from "node:os";

export interface AnimusConfig {
  serverUrl: string;
  unlockToken: string;
  username: string;
}

const DEFAULT_CONFIG_PATH = join(homedir(), ".animus", "config.json");

export function getConfigPath(): string {
  return DEFAULT_CONFIG_PATH;
}

export function readConfig(
  path: string = DEFAULT_CONFIG_PATH
): AnimusConfig | null {
  if (!existsSync(path)) return null;
  try {
    const raw = readFileSync(path, "utf-8");
    return JSON.parse(raw) as AnimusConfig;
  } catch {
    return null;
  }
}

export function writeConfig(
  path: string = DEFAULT_CONFIG_PATH,
  config: AnimusConfig
): void {
  const dir = dirname(path);
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true, mode: 0o700 });
  }
  writeFileSync(path, JSON.stringify(config, null, 2), { encoding: "utf-8", mode: 0o600 });
}

export async function login(
  serverUrl: string,
  username: string,
  password: string
): Promise<AnimusConfig> {
  const httpUrl = serverUrl
    .replace(/^ws/, "http")
    .replace(/\/ws\/agent$/, "");
  const res = await fetch(`${httpUrl}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Login failed: ${res.status} ${body}`);
  }

  const data = (await res.json()) as {
    unlockToken: string;
    username: string;
  };
  return {
    serverUrl,
    unlockToken: data.unlockToken,
    username: data.username,
  };
}
