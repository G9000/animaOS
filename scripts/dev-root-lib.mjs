import path from "node:path";

const DEFAULT_HEALTH_URL = "http://127.0.0.1:3031/health";
const DEFAULT_HEALTH_TIMEOUT_MS = 30000;
const DEFAULT_HEALTH_INTERVAL_MS = 500;

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

export async function startDevStack({
  spawn,
  waitForHealth: waitForHealthImpl = waitForHealth,
  healthUrl = DEFAULT_HEALTH_URL,
} = {}) {
  if (typeof spawn !== "function") {
    throw new Error("startDevStack requires a spawn function");
  }

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
