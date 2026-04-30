import { existsSync, mkdirSync } from "node:fs";
import { join, resolve } from "node:path";
import type { AnimusConfig } from "./client/auth";

export function getDefaultWorkspaceDir(baseDir: string = process.cwd()): string {
  return join(baseDir, ".anima", "workspace");
}

export function resolveWorkspaceDir(
  config: Pick<AnimusConfig, "workspaceDir"> | null | undefined,
  override?: string,
  baseDir: string = process.cwd(),
): string {
  return resolve(override || config?.workspaceDir || getDefaultWorkspaceDir(baseDir));
}

export function ensureWorkspaceDir(workspaceDir: string): string {
  const resolved = resolve(workspaceDir);
  if (!existsSync(resolved)) {
    mkdirSync(resolved, { recursive: true, mode: 0o700 });
  }
  return resolved;
}
