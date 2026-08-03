import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const REPO_ROOT = join(import.meta.dir, "..", "..", "..");
const WORKFLOWS = [
  "corefs-provenance.yml",
  "desktop-tests.yml",
  "server-ruff.yml",
  "server-tests.yml",
];

describe("GitHub Actions triggers", () => {
  test.each(WORKFLOWS)("%s is manual-only", (filename) => {
    const workflow = readFileSync(
      join(REPO_ROOT, ".github", "workflows", filename),
      "utf8",
    );

    expect(workflow).toMatch(/^on:\r?\n  workflow_dispatch:\s*$/m);
    expect(workflow).not.toMatch(/^\s+push:\s*$/m);
    expect(workflow).not.toMatch(/^\s+pull_request:\s*$/m);
  });
});
