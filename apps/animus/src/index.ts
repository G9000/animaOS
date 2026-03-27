#!/usr/bin/env bun
// apps/animus/src/index.ts
import { readConfig, writeConfig, login, getConfigPath } from "./client/auth";
import { runHeadless } from "./headless";

const args = process.argv.slice(2);
const serverFlag = args.indexOf("--server");
const serverUrl = serverFlag >= 0 ? args[serverFlag + 1] : undefined;

async function main() {
  let config = readConfig();

  // Override server URL if provided
  if (serverUrl && config) {
    config = { ...config, serverUrl };
  }

  // If no config, prompt for login
  if (!config) {
    const url = serverUrl || "ws://localhost:3031";
    console.log(`Connecting to ${url}`);
    console.log("Login required.");

    const readline = await import("node:readline");
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    const ask = (q: string): Promise<string> =>
      new Promise((resolve) => rl.question(q, resolve));

    const username = await ask("Username: ");
    const password = await ask("Password: ");
    rl.close();

    try {
      config = await login(url, username, password);
      writeConfig(getConfigPath(), config);
      console.log(`Logged in as ${config.username}. Config saved.`);
    } catch (err) {
      console.error(err instanceof Error ? err.message : String(err));
      process.exit(1);
    }
  }

  // Collect flags
  const flagArgs = new Set(["--server", "--json", "--timeout"]);
  const prompt = args.find((a, i) => {
    if (a.startsWith("--")) return false;
    // Skip values that follow a flag expecting a value
    const prev = args[i - 1];
    if (prev === "--server" || prev === "--timeout") return false;
    return true;
  });

  // Headless mode: first non-flag arg is the prompt
  if (prompt) {
    const jsonMode = args.includes("--json");
    const timeoutIdx = args.indexOf("--timeout");
    const timeout = timeoutIdx >= 0 ? parseInt(args[timeoutIdx + 1], 10) : undefined;

    await runHeadless({
      config,
      prompt,
      json: jsonMode,
      timeout: timeout && !isNaN(timeout) ? timeout : undefined,
    });
    return;
  }

  // Interactive TUI mode — lazy-load ink/React to keep headless path fast
  const { render } = await import("ink");
  const React = await import("react");
  const { App } = await import("./ui/App");
  render(React.createElement(App, { config }));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
