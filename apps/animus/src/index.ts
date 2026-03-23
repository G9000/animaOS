#!/usr/bin/env bun
// apps/animus/src/index.ts
import { render } from "ink";
import React from "react";
import { App } from "./ui/App";
import { readConfig, writeConfig, login, getConfigPath } from "./client/auth";

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

  // Headless mode: first non-flag arg is the prompt
  const prompt = args.find((a) => !a.startsWith("--") && a !== serverUrl);
  if (prompt) {
    // TODO: headless mode — connect, send prompt, print result, exit
    console.log("Headless mode not yet implemented. Use interactive mode.");
    process.exit(0);
  }

  // Interactive TUI mode
  render(React.createElement(App, { config }));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
