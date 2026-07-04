import { spawn } from "node:child_process";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { buildTargetSpec, startDevStack } from "./dev-root-lib.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const env = {
  ...process.env,
  NX_DAEMON: "false",
  NX_ISOLATE_PLUGINS: "false",
  NX_ADD_PLUGINS: "false",
};

let activeStack = null;
let exiting = false;
let stopWatchingServer = null;

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});

async function main() {
  activeStack = await startDevStack({
    spawn: ({ name }) => spawnNxDevTarget(name),
  });

  registerProcessHandlers(activeStack);
  stopWatchingServer = watchServerForRestart();
  const keepAlive = setInterval(() => {}, 1 << 30);
  const result = await createRootCompletion(activeStack).finally(() => {
    clearInterval(keepAlive);
  });

  if (exiting) {
    return;
  }

  exiting = true;
  stopWatchingServer?.();
  activeStack.stop();
  process.exit(normalizeExitCode(result.code, result.signal));
}

function spawnNxDevTarget(name) {
  const spec = buildTargetSpec(name, repoRoot, process.execPath);
  const child = spawn(spec.command, spec.args, {
    cwd: spec.cwd,
    env,
    stdio: "inherit",
  });

  return child;
}

function registerProcessHandlers(stack) {
  const stopAndExit = (exitCode) => {
    if (exiting) {
      return;
    }
    exiting = true;
    stopWatchingServer?.();
    stack.stop();
    process.exit(exitCode);
  };

  process.on("SIGINT", () => stopAndExit(130));
  process.on("SIGTERM", () => stopAndExit(143));
}

function normalizeExitCode(code, signal) {
  if (typeof code === "number") {
    return code;
  }
  if (signal === "SIGINT") {
    return 130;
  }
  if (signal === "SIGTERM") {
    return 143;
  }
  return 1;
}

function createRootCompletion(stack) {
  const [, desktop, animaMod] = stack.children;

  return Promise.race([
    waitForChildExit(desktop, "desktop"),
    waitForChildExit(animaMod, "anima-mod"),
  ]);
}

function waitForChildExit(child, name) {
  return new Promise((resolve, reject) => {
    child.once("exit", (code, signal) => {
      resolve({ name, code, signal });
    });
    child.once("error", reject);
  });
}

function watchServerForRestart() {
  const watchRoot = path.join(repoRoot, "apps", "server");
  let restartTimer = null;
  let restarting = false;

  const watcher = fs.watch(watchRoot, { recursive: true }, (_, filename) => {
    if (exiting || restarting || !shouldRestartServer(filename)) {
      return;
    }

    if (restartTimer) {
      clearTimeout(restartTimer);
    }

    restartTimer = setTimeout(async () => {
      restartTimer = null;
      restarting = true;
      try {
        await restartServerChild();
      } finally {
        restarting = false;
      }
    }, 250);
  });

  return () => {
    if (restartTimer) {
      clearTimeout(restartTimer);
      restartTimer = null;
    }
    watcher.close();
  };
}

function shouldRestartServer(filename) {
  if (!filename) {
    return false;
  }
  const normalized = String(filename).replaceAll("\\", "/").toLowerCase();
  if (normalized.includes("/__pycache__/")) {
    return false;
  }
  return normalized.endsWith(".py") || normalized.endsWith(".toml") || normalized.endsWith(".ini");
}

async function restartServerChild() {
  const current = activeStack?.children?.[0];
  if (!current || current.killed) {
    return;
  }

  await terminateProcessTree(current.pid);

  if (exiting) {
    return;
  }

  const next = spawnNxDevTarget("server");
  activeStack.children[0] = next;
}

function terminateProcessTree(pid) {
  return new Promise((resolve) => {
    const killer = spawn("taskkill", ["/PID", String(pid), "/T", "/F"], {
      stdio: "ignore",
      windowsHide: true,
    });
    killer.once("exit", () => resolve());
    killer.once("error", () => resolve());
  });
}
