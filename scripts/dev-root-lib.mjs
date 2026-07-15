import { randomBytes } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";

const DEFAULT_HEALTH_URL = "http://127.0.0.1:3031/health";
const DEFAULT_HEALTH_TIMEOUT_MS = 30000;
const DEFAULT_HEALTH_INTERVAL_MS = 500;
const SERVER_RESTART_EXTENSIONS = [".py", ".toml", ".ini"];

export function createDevSessionContinuity({
  tempRoot = os.tmpdir(),
  randomBytesImpl = randomBytes,
} = {}) {
  const directory = mkdtempSync(path.join(tempRoot, "anima-dev-session-"));
  const serverEnv = Object.freeze({
    ANIMA_DEV_SESSION_STATE_PATH: path.join(directory, "session-state.bin"),
    ANIMA_DEV_SESSION_KEY: randomBytesImpl(32).toString("base64"),
  });
  let cleaned = false;

  return Object.freeze({
    directory,
    serverEnv,
    cleanup() {
      if (cleaned) {
        return;
      }
      cleaned = true;
      rmSync(directory, { recursive: true, force: true });
    },
  });
}

export function buildTargetEnvironment(name, baseEnv, serverEnv) {
  const sanitizedBaseEnv = { ...baseEnv };
  for (const key of Object.keys(sanitizedBaseEnv)) {
    const normalizedKey = key.toUpperCase();
    if (
      normalizedKey === "ANIMA_DEV_SESSION_STATE_PATH" ||
      normalizedKey === "ANIMA_DEV_SESSION_KEY"
    ) {
      delete sanitizedBaseEnv[key];
    }
  }
  if (name === "server") {
    return { ...sanitizedBaseEnv, ...serverEnv };
  }
  return sanitizedBaseEnv;
}

export function createServerReloadScheduler({
  restart,
  waitForReady,
  onError,
  quietMs = 750,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
} = {}) {
  if (typeof restart !== "function" || typeof waitForReady !== "function") {
    throw new Error("Server reload scheduler requires restart and waitForReady callbacks");
  }

  let timer = null;
  let running = false;
  let pending = false;
  let stopped = false;
  const idleWaiters = new Set();

  const isIdle = () => timer === null && !running && !pending;
  const resolveIdleWaiters = () => {
    if (!isIdle()) {
      return;
    }
    for (const resolve of idleWaiters) {
      resolve();
    }
    idleWaiters.clear();
  };

  const arm = () => {
    if (stopped || running) {
      return;
    }
    if (timer !== null) {
      clearTimer(timer);
    }
    timer = setTimer(() => {
      timer = null;
      void run();
    }, quietMs);
  };

  const run = async () => {
    if (stopped) {
      pending = false;
      resolveIdleWaiters();
      return;
    }

    pending = false;
    running = true;
    try {
      await restart();
      await waitForReady();
    } catch (error) {
      pending = false;
      onError?.(error);
    } finally {
      running = false;
      if (!stopped && pending) {
        arm();
      }
      resolveIdleWaiters();
    }
  };

  return {
    schedule() {
      if (stopped) {
        return;
      }
      pending = true;
      if (!running) {
        arm();
      }
    },
    stop() {
      stopped = true;
      pending = false;
      if (timer !== null) {
        clearTimer(timer);
        timer = null;
      }
      resolveIdleWaiters();
    },
    whenIdle() {
      if (isIdle()) {
        return Promise.resolve();
      }
      return new Promise((resolve) => {
        idleWaiters.add(resolve);
      });
    },
  };
}

export function buildTargetSpec(name, repoRoot, nodeExecutable = process.execPath) {
  if (name === "server") {
    return {
      command: "uv",
      args: [
        "run",
        "--project",
        ".",
        "uvicorn",
        "anima_server.main:app",
        "--app-dir",
        "src",
        "--host",
        "127.0.0.1",
        "--port",
        "3031",
      ],
      cwd: path.join(repoRoot, "apps", "server"),
    };
  }

  if (name === "desktop") {
    return {
      command: "bun",
      args: ["run", "dev"],
      cwd: path.join(repoRoot, "apps", "desktop"),
    };
  }

  if (name === "anima-mod") {
    return {
      command: "bun",
      args: ["run", "dev"],
      cwd: path.join(repoRoot, "apps", "anima-mod"),
    };
  }

  throw new Error(`Unknown dev target: ${name}`);
}

export async function waitForHealth(
  url = DEFAULT_HEALTH_URL,
  {
    timeoutMs = DEFAULT_HEALTH_TIMEOUT_MS,
    intervalMs = DEFAULT_HEALTH_INTERVAL_MS,
    fetchImpl = globalThis.fetch,
  } = {},
) {
  if (typeof fetchImpl !== "function") {
    throw new Error("Global fetch is unavailable for runtime health checks");
  }

  const startedAt = Date.now();
  let lastError = null;

  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetchImpl(url, {
        headers: {
          Accept: "application/json",
        },
      });
      if (response.ok) {
        return;
      }
      lastError = new Error(`Runtime health returned HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }

    await sleep(intervalMs);
  }

  if (lastError instanceof Error) {
    throw lastError;
  }
  throw new Error(`Timed out waiting for runtime health at ${url}`);
}

export async function ensurePortAvailable(url = DEFAULT_HEALTH_URL) {
  const target = new URL(url);
  const host = target.hostname || "127.0.0.1";
  const port = Number(target.port || (target.protocol === "https:" ? 443 : 80));

  await new Promise((resolve, reject) => {
    const server = net.createServer();

    server.once("error", (error) => {
      if (error?.code === "EADDRINUSE") {
        reject(
          new Error(
            `Runtime port ${host}:${port} is already in use. Stop the existing server before running root bun dev.`,
          ),
        );
        return;
      }
      reject(error);
    });

    server.once("listening", () => {
      server.close((error) => {
        if (error) {
          reject(error);
          return;
        }
        resolve();
      });
    });

    server.listen(port, host);
  });
}

export function getServerRestartCandidate(filename) {
  if (!filename) {
    return null;
  }

  const normalized = String(filename).replaceAll("\\", "/").toLowerCase();
  const parts = normalized.split("/").filter(Boolean);
  if (parts.includes("__pycache__")) {
    return null;
  }

  if (SERVER_RESTART_EXTENSIONS.some((extension) => normalized.endsWith(extension))) {
    return normalized;
  }

  return null;
}

export async function startDevStack({
  ensureServerPortAvailable = ensurePortAvailable,
  spawn,
  waitForHealth: waitForHealthImpl = waitForHealth,
  healthUrl = DEFAULT_HEALTH_URL,
} = {}) {
  if (typeof spawn !== "function") {
    throw new Error("startDevStack requires a spawn function");
  }

  await ensureServerPortAvailable(healthUrl);

  const children = [];
  const server = spawn({ name: "server" });
  children.push(server);

  try {
    await waitForHealthImpl(healthUrl);
    children.push(spawn({ name: "desktop" }));
    children.push(spawn({ name: "anima-mod" }));
  } catch (error) {
    stopChildren(children);
    throw error;
  }

  return {
    children,
    completion: createCompletionPromise([
      { name: "server", child: children[0] },
      { name: "desktop", child: children[1] },
      { name: "anima-mod", child: children[2] },
    ]),
    stop() {
      stopChildren(children);
    },
  };
}

function stopChildren(children) {
  for (const child of [...children].reverse()) {
    if (!child || child.killed) {
      continue;
    }
    try {
      child.kill();
    } catch {
      // Ignore teardown races.
    }
  }
}

function createCompletionPromise(entries) {
  return new Promise((resolve, reject) => {
    for (const entry of entries) {
      if (!entry?.child || typeof entry.child.once !== "function") {
        continue;
      }

      entry.child.once("exit", (code, signal) => {
        resolve({
          name: entry.name,
          code,
          signal,
        });
      });

      entry.child.once("error", (error) => {
        reject(error);
      });
    }
  });
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}
