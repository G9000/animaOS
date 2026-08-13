import { describe, expect, test } from "bun:test";
import { extname, join, relative, resolve } from "node:path";
import { readdirSync, readFileSync, statSync } from "node:fs";
import ts from "typescript";

const desktopRoot = join(import.meta.dir, "..");
const repoRoot = resolve(desktopRoot, "..", "..");
const inventoryPath = join(repoRoot, "docs/architecture/system/portable-state-inventory.md");

function sourceFiles(root: string): string[] {
  const result: string[] = [];
  const visit = (directory: string) => {
    for (const name of readdirSync(directory)) {
      const path = join(directory, name);
      const stat = statSync(path);
      if (stat.isDirectory()) visit(path);
      else if ([".ts", ".tsx"].includes(extname(path))) result.push(path);
    }
  };
  visit(root);
  return result;
}

function inventoryKeys(store: "browser-local" | "browser-session"): Map<string, string> {
  const source = readFileSync(inventoryPath, "utf8");
  const start = "<!-- portable-state-inventory:v1:start -->";
  const end = "<!-- portable-state-inventory:v1:end -->";
  const body = source.split(start)[1]?.split(end)[0] ?? "";
  const result = new Map<string, string>();
  for (const rawLine of body.split("\n")) {
    const [rowStore, record, rawKeys, destination] = rawLine.trim().split("|");
    if (rowStore !== store || record !== "keys") continue;
    for (const key of rawKeys.split(",")) {
      expect(result.has(key)).toBe(false);
      result.set(key, destination);
    }
  }
  return result;
}

function stringConstants(file: ts.SourceFile): Map<string, string> {
  const result = new Map<string, string>();
  const visit = (node: ts.Node): void => {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.initializer &&
      (ts.isStringLiteral(node.initializer) || ts.isNoSubstitutionTemplateLiteral(node.initializer))
    ) {
      result.set(node.name.text, node.initializer.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return result;
}

function databaseStorageKeys(file: ts.SourceFile): string[] {
  const keys: string[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isTypeAliasDeclaration(node) && node.name.text === "DatabaseStorageKey") {
      if (!ts.isUnionTypeNode(node.type)) throw new Error("DatabaseStorageKey must stay explicit");
      for (const member of node.type.types) {
        if (!ts.isLiteralTypeNode(member) || !ts.isStringLiteral(member.literal)) {
          throw new Error("DatabaseStorageKey members must be string literals");
        }
        keys.push(member.literal.text);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return keys;
}

function resolveStorageArgument(
  path: string,
  expression: ts.Expression,
  constants: Map<string, string>,
  file: ts.SourceFile,
): string[] {
  if (ts.isStringLiteral(expression) || ts.isNoSubstitutionTemplateLiteral(expression)) {
    return [expression.text];
  }
  if (ts.isIdentifier(expression)) {
    const constant = constants.get(expression.text);
    if (constant) return [constant];
    if (expression.text === "DAEMON_CONTROL_TOKEN_ENV") return ["ANIMA_DAEMON_CONTROL_TOKEN"];
    if (path.endsWith("useLocalStorage.ts") && expression.text === "key") {
      return databaseStorageKeys(file);
    }
    if (path.endsWith("daemon.ts") && expression.text === "key") {
      return ["anima_daemon_control_token", "ANIMA_DAEMON_CONTROL_TOKEN"];
    }
    if (path.endsWith("draftMigration.ts")) {
      if (expression.text === "storageKey") return ["legacy-journal-draft:*"];
      if (expression.text === "stateKey") return ["anima:diary:draft-migration-state:v1:*"];
    }
  }
  if (ts.isCallExpression(expression) && ts.isIdentifier(expression.expression)) {
    if (expression.expression.text === "keyHintKey") return ["anima:key-hint:*"];
    if (expression.expression.text === "draftMigrationStateKey") {
      return ["anima:diary:draft-migration-state:v1:*"];
    }
  }
  throw new Error(`Unclassified storage key expression ${path}:${expression.getText(file)}`);
}

describe("portable settings storage classification", () => {
  test("classifies every renderer storage read and mutation", () => {
    const classified = {
      "browser-local": inventoryKeys("browser-local"),
      "browser-session": inventoryKeys("browser-session"),
    };
    const observed = {
      "browser-local": new Set<string>(),
      "browser-session": new Set<string>(),
    };

    for (const absolutePath of sourceFiles(join(desktopRoot, "src"))) {
      const path = relative(repoRoot, absolutePath);
      const source = readFileSync(absolutePath, "utf8");
      const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true);
      const constants = stringConstants(file);
      const visit = (node: ts.Node): void => {
        if (!ts.isCallExpression(node) || !ts.isPropertyAccessExpression(node.expression)) {
          ts.forEachChild(node, visit);
          return;
        }
        if (!["getItem", "setItem", "removeItem"].includes(node.expression.name.text)) {
          ts.forEachChild(node, visit);
          return;
        }
        const owner = node.expression.expression.getText(file);
        let store: "browser-local" | "browser-session" | null = null;
        if (owner === "localStorage") store = "browser-local";
        else if (owner === "sessionStorage") store = "browser-session";
        else if (owner === "storage" && path.endsWith("draftMigration.ts")) store = "browser-local";
        else if (owner === "storage" && path.endsWith("today-context.ts")) store = "browser-session";
        if (!store) {
          ts.forEachChild(node, visit);
          return;
        }
        const argument = node.arguments[0];
        if (!argument) throw new Error(`Missing storage key at ${path}`);
        for (const key of resolveStorageArgument(path, argument, constants, file)) {
          expect(classified[store].has(key), `${path} persists unclassified key ${key}`).toBe(true);
          observed[store].add(key);
        }
        ts.forEachChild(node, visit);
      };
      visit(file);
    }

    expect([...observed["browser-local"]].sort()).toEqual(
      [...classified["browser-local"].keys()].sort(),
    );
    expect([...observed["browser-session"]].sort()).toEqual(
      [...classified["browser-session"].keys()].sort(),
    );
  });

  test("keeps private browser values on removal, session, or credential paths", () => {
    const local = inventoryKeys("browser-local");
    expect(local.get("anima_user")).toBe("remove-private-profile-cache");
    expect(local.get("anima_unlock_token")).toBe("remove-legacy-session");
    expect(local.get("anima_daemon_control_token")).toBe("os-credential");
    expect(local.get("legacy-journal-draft:*")).toBe("corefs-object");
  });
});
