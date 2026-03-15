const { spawnSync } = require("node:child_process");
const path = require("node:path");

const args = process.argv.slice(2);

if (args.length === 0) {
  console.error("Usage: node scripts/run-uv.cjs <uv args...>");
  process.exit(1);
}

const repoRoot = path.resolve(__dirname, "..");
const env = {
  ...process.env,
  UV_CACHE_DIR: process.env.UV_CACHE_DIR || path.join(repoRoot, ".uv-cache"),
};

const result = spawnSync("uv", args, {
  stdio: "inherit",
  cwd: process.cwd(),
  env,
});

if (typeof result.status === "number") {
  process.exit(result.status);
}

if (result.error) {
  throw result.error;
}

process.exit(1);
