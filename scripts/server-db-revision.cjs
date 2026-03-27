const { spawnSync } = require("node:child_process");

const message = process.argv.slice(2).join(" ").trim();

if (!message) {
  console.error("Usage: bun run db:server:revision -- <message>");
  process.exit(1);
}

const command = [
  "uv",
  "run",
  "--project",
  "apps/server",
  "alembic",
  "-c",
  "apps/server/alembic_core.ini",
  "revision",
  "--autogenerate",
  "-m",
  message,
];

const result = spawnSync(command[0], command.slice(1), {
  stdio: "inherit",
  cwd: process.cwd(),
  env: process.env,
  shell: false,
});

if (typeof result.status === "number") {
  process.exit(result.status);
}

if (result.error) {
  throw result.error;
}

process.exit(1);
