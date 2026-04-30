#!/usr/bin/env bun
// apps/animus/src/index.ts
import { readConfig, writeConfig, login, getConfigPath } from "./client/auth";
import { runHeadless } from "./headless";
import { parseCliArgs } from "./cli";
import {
  ensureWorkspaceDir,
  resolveWorkspaceDir,
} from "./workspace";

const args = process.argv.slice(2);
let parsedArgs: ReturnType<typeof parseCliArgs>;
try {
  parsedArgs = parseCliArgs(args);
} catch (err) {
  console.error(err instanceof Error ? `Error: ${err.message}` : String(err));
  process.exit(1);
}

async function main() {
  let config = readConfig();

  if (parsedArgs.configCommand?.type === "show") {
    const workspaceDir = resolveWorkspaceDir(config, parsedArgs.workspaceOverride);
    const safeConfig = config
      ? { ...config, unlockToken: config.unlockToken ? "[redacted]" : "", workspaceDir }
      : { configPath: getConfigPath(), workspaceDir };
    console.log(JSON.stringify(safeConfig, null, 2));
    return;
  }

  if (parsedArgs.configCommand?.type === "set-workspace") {
    if (!config) {
      console.error("No Animus config found. Run `anima` once to log in before setting a workspace.");
      process.exit(1);
    }

    const workspaceDir = ensureWorkspaceDir(parsedArgs.configCommand.workspaceDir);
    config = { ...config, workspaceDir };
    writeConfig(getConfigPath(), config);
    console.log(`Workspace set to ${workspaceDir}`);
    return;
  }

  // Override server URL if provided
  if (parsedArgs.serverUrl && config) {
    config = { ...config, serverUrl: parsedArgs.serverUrl };
  }

  // If no config, prompt for login
  if (!config) {
    const url = parsedArgs.serverUrl || "ws://localhost:3031";
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
      config = { ...config, workspaceDir: resolveWorkspaceDir(config) };
      writeConfig(getConfigPath(), config);
      console.log(`Logged in as ${config.username}. Config saved.`);
    } catch (err) {
      console.error(err instanceof Error ? err.message : String(err));
      process.exit(1);
    }
  }

  if (!config.workspaceDir) {
    config = { ...config, workspaceDir: resolveWorkspaceDir(config) };
    writeConfig(getConfigPath(), config);
  }

  const workspaceDir = ensureWorkspaceDir(
    resolveWorkspaceDir(config, parsedArgs.workspaceOverride),
  );
  config = { ...config, workspaceDir };
  process.chdir(workspaceDir);

  // Headless mode: first non-flag arg is the prompt
  if (parsedArgs.prompt) {
    await runHeadless({
      config,
      prompt: parsedArgs.prompt,
      json: parsedArgs.json,
      plan: parsedArgs.plan,
      timeout: parsedArgs.timeout,
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
