import { describe, test, expect, beforeEach, afterEach } from "bun:test";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import {
  ensureWorkspaceDir,
  getDefaultWorkspaceDir,
  resolveWorkspaceDir,
} from "./workspace";

describe("Animus workspace", () => {
  let tempDir: string;

  beforeEach(() => {
    tempDir = mkdtempSync(join(tmpdir(), "animus-workspace-test-"));
  });

  afterEach(() => {
    rmSync(tempDir, { recursive: true, force: true });
  });

  test("default workspace lives under .anima/workspace in the launch directory", () => {
    expect(getDefaultWorkspaceDir(tempDir)).toBe(join(tempDir, ".anima", "workspace"));
  });

  test("configured workspace overrides the default", () => {
    const configured = join(tempDir, "configured");

    expect(resolveWorkspaceDir({ workspaceDir: configured }, undefined, tempDir)).toBe(
      resolve(configured),
    );
  });

  test("runtime workspace override wins over configured workspace", () => {
    const configured = join(tempDir, "configured");
    const override = join(tempDir, "override");

    expect(resolveWorkspaceDir({ workspaceDir: configured }, override, tempDir)).toBe(
      resolve(override),
    );
  });

  test("ensureWorkspaceDir creates the folder and returns the resolved path", () => {
    const workspaceDir = join(tempDir, "nested", "workspace");

    expect(ensureWorkspaceDir(workspaceDir)).toBe(resolve(workspaceDir));
    expect(existsSync(workspaceDir)).toBe(true);
  });
});
