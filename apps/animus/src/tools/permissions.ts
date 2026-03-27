// apps/animus/src/tools/permissions.ts
import { resolve, relative } from "node:path";

export type PermissionDecision = "allow" | "deny" | "ask";

const READ_ONLY_TOOLS = new Set(["read_file", "grep", "glob", "list_dir"]);
const WRITE_TOOLS = new Set(["write_file", "edit_file"]);

const SAFE_BASH_PATTERNS = [
  /^(ls|pwd|echo|cat|head|tail|wc|date|whoami|which|type|file)\b/,
  /^git\s+(status|log|diff|branch|show|remote|tag)\b/,
  /^(node|python|bun|npm|pip)\s+--version$/,
];

const DANGEROUS_BASH_PATTERNS = [
  /^(rm|rmdir)\s/,
  /^sudo\b/,
  /^git\s+(push|reset|rebase|force)/,
  /^(chmod|chown)\s/,
  /\|\s*sh\b/,
  />\s*\/dev\/sd/,
];

const sessionRules: Set<string> = new Set();

export function addSessionRule(rule: string): void {
  sessionRules.add(rule);
}

export function clearSessionRules(): void {
  sessionRules.clear();
}

export function checkPermission(
  toolName: string,
  args: Record<string, unknown>,
): PermissionDecision {
  if (toolName === "ask_user") return "allow";
  if (READ_ONLY_TOOLS.has(toolName)) return "allow";
  if (sessionRules.has(toolName)) return "allow";

  if (WRITE_TOOLS.has(toolName)) {
    const filePath = args.file_path as string | undefined;
    if (filePath) {
      const rel = relative(process.cwd(), resolve(filePath));
      if (rel.startsWith("..")) return "ask";
    }
    return "allow";
  }

  if (toolName === "bash") {
    const command = ((args.command as string) || "").trim();
    if (sessionRules.has(`bash:${command}`)) return "allow";
    if (SAFE_BASH_PATTERNS.some((p) => p.test(command))) return "allow";
    if (DANGEROUS_BASH_PATTERNS.some((p) => p.test(command))) return "ask";
    return "ask";
  }

  return "ask";
}
