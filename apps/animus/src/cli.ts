export type ConfigCommand =
  | { type: "show" }
  | { type: "set-workspace"; workspaceDir: string };

export interface ParsedCliArgs {
  serverUrl?: string;
  workspaceOverride?: string;
  prompt?: string;
  json: boolean;
  plan: boolean;
  timeout?: number;
  configCommand?: ConfigCommand;
}

const VALUE_FLAGS = new Set(["--server", "--timeout", "--workspace"]);

function flagValue(args: string[], flag: string, label: string): string | undefined {
  const index = args.indexOf(flag);
  if (index < 0) return undefined;

  const value = args[index + 1];
  if (!value || value.startsWith("--")) {
    throw new Error(`${flag} requires ${label} argument`);
  }
  return value;
}

export function parseCliArgs(args: string[]): ParsedCliArgs {
  const serverUrl = flagValue(args, "--server", "a URL");
  const workspaceOverride = flagValue(args, "--workspace", "a directory");
  const timeoutRaw = flagValue(args, "--timeout", "a millisecond timeout");
  const timeout = timeoutRaw ? parseInt(timeoutRaw, 10) : undefined;

  if (args[0] === "config") {
    const command = args[1];
    if (command === "show") {
      return {
        serverUrl,
        workspaceOverride,
        json: args.includes("--json"),
        plan: args.includes("--plan"),
        timeout: timeout && !isNaN(timeout) ? timeout : undefined,
        configCommand: { type: "show" },
      };
    }

    if (command === "set-workspace") {
      const workspaceDir = args[2];
      if (!workspaceDir || workspaceDir.startsWith("--")) {
        throw new Error("config set-workspace requires a directory argument");
      }
      return {
        serverUrl,
        workspaceOverride,
        json: args.includes("--json"),
        plan: args.includes("--plan"),
        timeout: timeout && !isNaN(timeout) ? timeout : undefined,
        configCommand: { type: "set-workspace", workspaceDir },
      };
    }

    throw new Error("Unknown config command. Use `config show` or `config set-workspace <path>`.");
  }

  const prompt = args.find((arg, index) => {
    if (arg.startsWith("--")) return false;
    const previous = args[index - 1];
    if (VALUE_FLAGS.has(previous)) return false;
    return true;
  });

  return {
    serverUrl,
    workspaceOverride,
    prompt,
    json: args.includes("--json"),
    plan: args.includes("--plan"),
    timeout: timeout && !isNaN(timeout) ? timeout : undefined,
  };
}
