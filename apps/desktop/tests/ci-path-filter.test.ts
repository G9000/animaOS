import { describe, expect, test } from "bun:test";
import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

/**
 * The desktop CI gate must trigger for every workspace package the desktop
 * app can break on — including TRANSITIVE ones (PR #133 review: api-client
 * re-exports @anima/auth-contracts types, so a contract change breaks the
 * desktop typecheck while the gate stayed silent).
 *
 * Rather than maintaining that list by hand, this test derives the closure
 * from package.json files and fails when the workflow's path filter misses
 * a member — so a new dependency edge is caught by CI, not by a reviewer.
 */
const REPO_ROOT = join(import.meta.dir, "..", "..", "..");
const WORKFLOW = join(REPO_ROOT, ".github", "workflows", "desktop-tests.yml");
const PACKAGES_DIR = join(REPO_ROOT, "packages");

function readPackageJson(path: string): { name?: string; dependencies?: Record<string, string> } {
  return JSON.parse(readFileSync(path, "utf8"));
}

/** @anima/<name> → packages/<dir>, from each package's declared name. */
function workspaceIndex(): Map<string, string> {
  const index = new Map<string, string>();
  for (const dir of readdirSync(PACKAGES_DIR)) {
    const manifest = join(PACKAGES_DIR, dir, "package.json");
    if (!existsSync(manifest)) continue;
    const name = readPackageJson(manifest).name;
    if (name) index.set(name, dir);
  }
  return index;
}

function desktopDependencyClosure(): Set<string> {
  const index = workspaceIndex();
  const seen = new Set<string>();
  const queue = Object.keys(
    readPackageJson(join(REPO_ROOT, "apps", "desktop", "package.json")).dependencies ?? {},
  ).filter((name) => index.has(name));

  while (queue.length > 0) {
    const name = queue.shift() as string;
    const dir = index.get(name);
    if (!dir || seen.has(dir)) continue;
    seen.add(dir);
    const deps = readPackageJson(join(PACKAGES_DIR, dir, "package.json")).dependencies ?? {};
    for (const dep of Object.keys(deps)) {
      const depDir = index.get(dep);
      if (depDir && !seen.has(depDir)) queue.push(dep);
    }
  }
  return seen;
}

describe("desktop CI path filter (RWF-008)", () => {
  test("covers every workspace package the desktop transitively depends on", () => {
    const workflow = readFileSync(WORKFLOW, "utf8");
    const missing = [...desktopDependencyClosure()]
      .filter((dir) => !workflow.includes(`"packages/${dir}/**"`))
      .sort();
    expect(missing).toEqual([]);
  });

  test("the closure actually includes a transitive package", () => {
    // Guards the guard: if this ever returns only direct dependencies the
    // test above would pass vacuously for transitive misses.
    const closure = desktopDependencyClosure();
    expect(closure.has("api-client")).toBe(true);
    expect(closure.has("anima-auth-contracts")).toBe(true);
  });
});
