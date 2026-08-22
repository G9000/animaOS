import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import {
  buildTargetEnvironment,
  buildTargetSpec,
  createDevSessionContinuity,
  createServerReloadScheduler,
  getServerRestartCandidate,
  startDevStack,
  waitForHealth,
} from "./dev-root-lib.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const env = {
  ...process.env,
  NX_DAEMON: "false",
  NX_ISOLATE_PLUGINS: "false",
  NX_ADD_PLUGINS: "false",
};
const credentialBrokerSecret = randomBytes(48).toString("base64url");

let activeStack = null;
let devSessionContinuity = null;
let exiting = false;
let stopWatchingServer = null;

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  cleanupDevSessionContinuity();
  process.exit(1);
});

async function main() {
  devSessionContinuity = createDevSessionContinuity();
  registerProcessHandlers();
  activeStack = await startDevStack({
    spawn: ({ name }) => spawnNxDevTarget(name),
  });

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
  cleanupDevSessionContinuity();
  process.exit(normalizeExitCode(result.code, result.signal));
}

function spawnNxDevTarget(name) {
  const spec = buildTargetSpec(name, repoRoot, process.execPath);
  const child = spawn(spec.command, spec.args, {
    cwd: spec.cwd,
    env: buildTargetEnvironment(
      name,
      env,
      devSessionContinuity?.serverEnv ?? {},
      credentialBrokerSecret,
    ),
    stdio: "inherit",
  });

  return child;
}

function registerProcessHandlers() {
  process.on("SIGINT", () => stopAndExit(130));
  process.on("SIGTERM", () => stopAndExit(143));
}

function stopAndExit(exitCode) {
  if (exiting) {
    return;
  }
  exiting = true;
  stopWatchingServer?.();
  activeStack?.stop();
  cleanupDevSessionContinuity();
  process.exit(exitCode);
}

function cleanupDevSessionContinuity() {
  devSessionContinuity?.cleanup();
  devSessionContinuity = null;
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
  let latestTrigger = null;

  const scheduler = createServerReloadScheduler({
    restart: async () => {
      const trigger = latestTrigger;
      latestTrigger = null;
      console.log(
        `[dev-root] restarting server after ${trigger?.eventType ?? "change"} on apps/server/${trigger?.path ?? "unknown"}`,
      );
      await restartServerChild();
    },
    waitForReady: async () => {
      await waitForHealth();
      console.log("[dev-root] server reload ready at http://127.0.0.1:3031/health");
    },
    onError: (error) => {
      console.error(
        `[dev-root] server reload failed: ${error instanceof Error ? error.message : error}`,
      );
      stopAndExit(1);
    },
  });

  const watcher = fs.watch(watchRoot, { recursive: true }, (eventType, filename) => {
    const restartCandidate = getServerRestartCandidate(filename);
    if (exiting || !restartCandidate) {
      return;
    }
    latestTrigger = { eventType, path: restartCandidate };
    scheduler.schedule();
  });

  return () => {
    scheduler.stop();
    watcher.close();
  };
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
