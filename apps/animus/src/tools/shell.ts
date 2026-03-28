// apps/animus/src/tools/shell.ts
import { existsSync } from "node:fs";

export type ShellLauncher = [executable: string, ...flags: string[]];

let cached: ShellLauncher | null = null;

/** Returns [executable, ...flags] for Bun.spawn([...launcher, command]). Cached for process lifetime. */
export function getShellLauncher(): ShellLauncher {
  if (cached) return cached;
  cached = resolve();
  return cached;
}

/** Reset cache — for testing only. */
export function _resetCache(): void {
  cached = null;
}

function resolve(): ShellLauncher {
  const platform = process.platform;

  if (platform === "win32") {
    return resolveWindows();
  }
  if (platform === "darwin") {
    return resolveDarwin();
  }
  return resolveLinux();
}

function resolveDarwin(): ShellLauncher {
  // Prefer zsh on macOS — avoids bash 3.2 HEREDOC apostrophe bug
  const candidates: Array<[string, string[]]> = [
    ["/bin/zsh", ["-lc"]],
    ["/bin/bash", ["-c"]],
  ];
  return probeFirst(candidates);
}

function resolveLinux(): ShellLauncher {
  const candidates: Array<[string, string[]]> = [];

  // Respect $SHELL if set
  const userShell = process.env.SHELL;
  if (userShell) {
    const flags = shellFlags(userShell);
    candidates.push([userShell, flags]);
  }

  candidates.push(
    ["/bin/bash", ["-c"]],
    ["/usr/bin/bash", ["-c"]],
    ["/bin/zsh", ["-c"]],
    ["/bin/sh", ["-c"]],
  );
  return probeFirst(candidates);
}

function resolveWindows(): ShellLauncher {
  const candidates: Array<[string, string[]]> = [
    ["powershell.exe", ["-NoProfile", "-Command"]],
    ["pwsh", ["-NoProfile", "-Command"]],
  ];

  // Respect ComSpec for cmd.exe
  const comspec = process.env.ComSpec || "cmd.exe";
  candidates.push([comspec, ["/d", "/s", "/c"]]);

  return probeFirst(candidates);
}

function shellFlags(shell: string): string[] {
  const base = shell.split("/").pop() || "";
  if (base === "bash" || base === "zsh") return ["-lc"];
  return ["-c"];
}

function probeFirst(candidates: Array<[string, string[]]>): ShellLauncher {
  const tried: string[] = [];
  for (const [exe, flags] of candidates) {
    tried.push(exe);
    if (exe.startsWith("/")) {
      if (existsSync(exe)) return [exe, ...flags];
    } else {
      // For non-absolute paths (Windows), check via Bun.which
      if (typeof Bun !== "undefined" && Bun.which(exe)) return [exe, ...flags];
      // Fallback: assume it exists (will fail at spawn time with clear error)
      return [exe, ...flags];
    }
  }
  throw new Error(`No shell found. Tried: ${tried.join(", ")}`);
}
