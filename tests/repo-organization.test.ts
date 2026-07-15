import { afterEach, describe, expect, test } from "bun:test";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";

import {
  CANONICAL_STATUSES,
  collectOrganizationViolations,
  parseTicketDocument,
  renderReport,
  runOrganizationCheck,
  type RepositorySnapshot,
} from "../scripts/check-repo-organization";

const temporaryRoots: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryRoots.splice(0).map((root) =>
      rm(root, { force: true, recursive: true }),
    ),
  );
});

async function write(root: string, relativePath: string, contents = "") {
  const path = join(root, ...relativePath.split("/"));
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, contents, "utf8");
}

function parentDocument(
  rows: string,
  heading = "Child Ticket Order",
  header = "| Ticket | Title | Status | Depends on |",
) {
  return [
    "# ABC-000 - Parent",
    "",
    "- Status: in_progress",
    "- Completed:",
    "",
    `## ${heading}`,
    "",
    header,
    "| --- | --- | --- | --- |",
    rows,
    "",
  ].join("\n");
}

function childDocument(
  status = "done",
  parent = "ABC-000",
  completed = "2026-07-15 10:00 MYT",
) {
  return [
    "# ABC-001 - Child",
    "",
    `- Status: ${status}`,
    `- Parent: \`${parent}\``,
    `- Completed: ${completed}`,
    "",
    "## Activity Log",
    "",
    "- Previously moved from todo to in-review before reaching done.",
    "",
  ].join("\n");
}

async function createCleanSnapshot(): Promise<RepositorySnapshot> {
  const root = await mkdtemp(join(tmpdir(), "anima-repo-organization-"));
  temporaryRoots.push(root);

  await write(
    root,
    "tickets/TEMPLATE.md",
    [
      "# TICKET-ID - Ticket title",
      "",
      "- Status: backlog",
      "- Parent: `PREFIX-000`",
      "- PRD: none",
      "- Spec: none",
      "- Plan: none",
      "- Completed:",
      "",
      "## Goal",
      "",
    ].join("\n"),
  );
  await write(
    root,
    "tickets/example/ABC-000-parent.md",
    parentDocument("| `ABC-001` | Child | `done` | none |"),
  );
  await write(
    root,
    "tickets/example/ABC-001-child.md",
    childDocument(),
  );
  await write(
    root,
    "tickets/example/README.md",
    "# Example tickets\n\n- Status: todo\n",
  );
  await write(root, "apps/sample/package.json", "{}\n");
  await write(root, "packages/sample/Cargo.toml", "[package]\nname = \"sample\"\n");
  await mkdir(join(root, "docs", "audit"), { recursive: true });
  await write(
    root,
    "scratchboard/README.md",
    "# Scratchboard\n\nThis directory is LEGACY and retained for older workstreams.\n",
  );

  return { root, trackedFiles: new Set<string>() };
}

async function violationsFor(snapshot: RepositorySnapshot, check: string) {
  return (await collectOrganizationViolations(snapshot)).filter(
    (violation) => violation.check === check,
  );
}

describe("ticket parsing", () => {
  test("exports exactly the canonical ticket statuses", () => {
    expect([...CANONICAL_STATUSES]).toEqual([
      "backlog",
      "in_progress",
      "blocked",
      "done",
    ]);
  });

  test("reads ticket metadata only before the first section", () => {
    const parsed = parseTicketDocument(
      "tickets/example/ABC-001-child.md",
      [
        "# ABC-001 - Child title",
        "- Status: done",
        "- Parent: `ABC-000`",
        "- Completed: 2026-07-15 10:00 MYT",
        "## Activity Log",
        "- Status: todo",
        "- Parent: `WRONG-000`",
        "- Completed:",
      ].join("\n"),
    );

    expect(parsed.ticketId).toBe("ABC-001");
    expect(parsed.title).toBe("Child title");
    expect(parsed.status).toBe("done");
    expect(parsed.parent).toBe("ABC-000");
    expect(parsed.completed).toBe("2026-07-15 10:00 MYT");
    expect(parsed.parentRows).toEqual([]);
  });

  test("parses CRLF Child Tickets tables with derived columns and unquoted cells", () => {
    const parsed = parseTicketDocument(
      "tickets/example/ABC-000-parent.md",
      [
        "# ABC-000 - Parent",
        "- Status: in_progress",
        "",
        "## Child Tickets",
        "| Title | Status | Ticket | Depends on |",
        "| --- | --- | --- | --- |",
        "| First child | done | ABC-001 | none |",
      ].join("\r\n"),
    );

    expect(parsed.parentRows).toEqual([
      { ticketId: "ABC-001", status: "done" },
    ]);
  });

  test("parses LF Child Ticket Order tables with quoted cells", () => {
    const parsed = parseTicketDocument(
      "tickets/example/ABC-000-parent.md",
      parentDocument(
        "| `ABC-001` | none | `blocked` | Child |",
        "Child Ticket Order",
        "| Ticket | Depends on | Status | Title |",
      ),
    );

    expect(parsed.parentRows).toEqual([
      { ticketId: "ABC-001", status: "blocked" },
    ]);
  });

  test("does not split an escaped pipe inside a parent-row title", () => {
    const parsed = parseTicketDocument(
      "tickets/example/ABC-000-parent.md",
      [
        "# ABC-000 - Parent",
        "- Status: in_progress",
        "## Child Tickets",
        "| Ticket | Title | Status |",
        "| --- | --- | --- |",
        "| ABC-001 | Parser A \\| Parser B | done |",
      ].join("\n"),
    );

    expect(parsed.parentRows).toEqual([
      { ticketId: "ABC-001", status: "done" },
    ]);
  });

  test("does not split a pipe inside inline code in a parent-row title", () => {
    const parsed = parseTicketDocument(
      "tickets/example/ABC-000-parent.md",
      [
        "# ABC-000 - Parent",
        "- Status: in_progress",
        "## Child Tickets",
        "| Ticket | Title | Status |",
        "| --- | --- | --- |",
        "| ABC-001 | `A | B` | done |",
      ].join("\n"),
    );

    expect(parsed.parentRows).toEqual([
      { ticketId: "ABC-001", status: "done" },
    ]);
  });

  test("finds the authoritative table after explanatory parent prose", () => {
    const parsed = parseTicketDocument(
      "tickets/example/ABC-000-parent.md",
      [
        "# ABC-000 - Parent",
        "- Status: in_progress",
        "",
        "## Child Ticket Order",
        "",
        "This table documents execution order.",
        "",
        "| Ticket | Status |",
        "| --- | --- |",
        "| `ABC-001` | `done` |",
      ].join("\n"),
    );

    expect(parsed.hasAuthoritativeChildTable).toBeTrue();
    expect(parsed.parentRows).toEqual([
      { ticketId: "ABC-001", status: "done" },
    ]);
  });

  test("treats Parent none as the no-parent sentinel", () => {
    const parsed = parseTicketDocument(
      "tickets/example/ABC-000-parent.md",
      "# ABC-000 - Parent\n- Status: in_progress\n- Parent: none\n",
    );

    expect(parsed.parent).toBeUndefined();
  });
});

describe("ticket organization checks", () => {
  test("accepts every canonical status in ticket headers and parent rows", async () => {
    const snapshot = await createCleanSnapshot();
    for (const [index, status] of [...CANONICAL_STATUSES].entries()) {
      await write(
        snapshot.root,
        `tickets/canonical/CAN-10${index}-${status}.md`,
        `# CAN-10${index} - Canonical\n- Status: ${status}\n`,
      );
    }
    await write(
      snapshot.root,
      "tickets/canonical/CAN-000-parent.md",
      [
        "# CAN-000 - Parent",
        "- Status: in_progress",
        "## Child Tickets",
        "| Ticket | Status |",
        "| --- | --- |",
        ...[...CANONICAL_STATUSES].map(
          (status, index) => `| CAN-20${index} | ${status} |`,
        ),
      ].join("\n"),
    );

    expect(await violationsFor(snapshot, "ticket-status")).toEqual([]);
  });

  test("rejects legacy and unknown statuses only in authoritative fields", async () => {
    const snapshot = await createCleanSnapshot();
    const invalid = ["todo", "in-review", "in_review", "unknown"];

    for (const [index, status] of invalid.entries()) {
      await write(
        snapshot.root,
        `tickets/invalid/INV-10${index}-${status}.md`,
        `# INV-10${index} - Invalid\n- Status: ${status}\n\n## Activity Log\n- Status: done\n`,
      );
    }
    await write(
      snapshot.root,
      "tickets/invalid/INV-000-parent.md",
      [
        "# INV-000 - Parent",
        "- Status: in_progress",
        "## Child Tickets",
        "| Ticket | Status |",
        "| --- | --- |",
        ...invalid.map((status, index) => `| INV-20${index} | ${status} |`),
        "## Activity Log",
        "- Moved from todo and in-review to done.",
      ].join("\n"),
    );

    const violations = await violationsFor(snapshot, "ticket-status");

    expect(violations).toHaveLength(8);
    expect(violations.map(({ message }) => message).join("\n")).toContain(
      '"todo"',
    );
    expect(violations.map(({ message }) => message).join("\n")).toContain(
      '"in-review"',
    );
    expect(violations.map(({ message }) => message).join("\n")).toContain(
      '"in_review"',
    );
    expect(violations.map(({ message }) => message).join("\n")).toContain(
      '"unknown"',
    );
    expect(
      violations.some(({ path }) => path.endsWith("README.md")),
    ).toBeFalse();
  });

  test("requires done when Completed is nonempty but allows done without Completed", async () => {
    const snapshot = await createCleanSnapshot();
    await write(
      snapshot.root,
      "tickets/example/ABC-000-parent.md",
      parentDocument("| `ABC-001` | Child | `in_progress` | none |"),
    );
    await write(
      snapshot.root,
      "tickets/example/ABC-001-child.md",
      childDocument("in_progress"),
    );
    await write(
      snapshot.root,
      "tickets/example/LEG-001-done-without-completed.md",
      "# LEG-001 - Historical ticket\n- Status: done\n- Completed:\n",
    );

    const violations = await violationsFor(snapshot, "ticket-completion");

    expect(violations).toHaveLength(1);
    expect(violations[0]?.path).toBe("tickets/example/ABC-001-child.md");
  });

  test("accepts a unique parent row whose child parent and status match", async () => {
    const snapshot = await createCleanSnapshot();

    expect(await violationsFor(snapshot, "ticket-links")).toEqual([]);
  });

  test("reports parent rows whose child is missing or ambiguous", async () => {
    const snapshot = await createCleanSnapshot();
    await write(
      snapshot.root,
      "tickets/example/ABC-000-parent.md",
      parentDocument(
        [
          "| `ABC-001` | Child | `done` | none |",
          "| `ABC-999` | Missing | `backlog` | none |",
        ].join("\n"),
      ),
    );
    await write(
      snapshot.root,
      "tickets/duplicate/ABC-001-second.md",
      childDocument(),
    );

    const messages = (await violationsFor(snapshot, "ticket-links")).map(
      ({ message }) => message,
    );

    expect(messages.some((message) => message.includes("ABC-999") && message.includes("no child"))).toBeTrue();
    expect(messages.some((message) => message.includes("ABC-001") && message.includes("ambiguous"))).toBeTrue();
  });

  test("reports parent rows whose child Parent or status disagrees", async () => {
    const snapshot = await createCleanSnapshot();
    await write(
      snapshot.root,
      "tickets/example/ABC-000-parent.md",
      parentDocument("| `ABC-001` | Child | `backlog` | none |"),
    );
    await write(
      snapshot.root,
      "tickets/example/ABC-001-child.md",
      childDocument("done", "XYZ-000"),
    );

    const messages = (await violationsFor(snapshot, "ticket-links")).map(
      ({ message }) => message,
    );

    expect(messages.some((message) => message.includes("Parent") && message.includes("XYZ-000"))).toBeTrue();
    expect(messages.some((message) => message.includes("status") && message.includes("backlog") && message.includes("done"))).toBeTrue();
  });

  test("reports a child omitted from its conforming parent's table", async () => {
    const snapshot = await createCleanSnapshot();
    await write(
      snapshot.root,
      "tickets/example/ABC-000-parent.md",
      parentDocument(""),
    );

    const messages = (await violationsFor(snapshot, "ticket-links")).map(
      ({ message }) => message,
    );

    expect(messages.some((message) => message.includes("ABC-001") && message.includes("zero rows"))).toBeTrue();
  });

  test("reports a child duplicated in its conforming parent's table", async () => {
    const snapshot = await createCleanSnapshot();
    await write(
      snapshot.root,
      "tickets/example/ABC-000-parent.md",
      parentDocument(
        [
          "| `ABC-001` | Child | `done` | none |",
          "| `ABC-001` | Child again | `done` | none |",
        ].join("\n"),
      ),
    );

    const messages = (await violationsFor(snapshot, "ticket-links")).map(
      ({ message }) => message,
    );

    expect(messages.some((message) => message.includes("ABC-001") && message.includes("2 rows"))).toBeTrue();
  });

  test("requires PRD, Spec, and Plan only in the ticket template", async () => {
    for (const missingField of ["PRD", "Spec", "Plan"]) {
      const snapshot = await createCleanSnapshot();
      const fields = ["PRD", "Spec", "Plan"]
        .filter((field) => field !== missingField)
        .map((field) => `- ${field}: none`);
      await write(
        snapshot.root,
        "tickets/TEMPLATE.md",
        [
          "# TICKET-ID",
          "- Status: backlog",
          ...fields,
          "",
          "## Notes",
          `- ${missingField}: none`,
          "",
        ].join("\n"),
      );
      await write(
        snapshot.root,
        "tickets/example/LEG-002-no-spec.md",
        "# LEG-002 - Legacy\n- Status: backlog\n- PRD: none\n- Plan: none\n",
      );

      expect(await violationsFor(snapshot, "ticket-template")).toEqual([
        expect.objectContaining({
          path: "tickets/TEMPLATE.md",
          message: expect.stringContaining(missingField),
        }),
      ]);
    }
  });
});

describe("repository hygiene checks", () => {
  test("recognizes every supported direct app and package manifest", async () => {
    const snapshot = await createCleanSnapshot();
    await write(snapshot.root, "apps/package-project/package.json", "{}\n");
    await write(snapshot.root, "apps/nx-project/project.json", "{}\n");
    await write(snapshot.root, "packages/python-project/pyproject.toml", "\n");
    await write(snapshot.root, "packages/rust-project/Cargo.toml", "\n");

    expect(await violationsFor(snapshot, "project-manifest")).toEqual([]);
  });

  test("reports a direct app or package without a supported manifest", async () => {
    const snapshot = await createCleanSnapshot();
    await write(snapshot.root, "apps/missing/README.md", "# Missing\n");
    await write(snapshot.root, "packages/missing/src/index.ts", "\n");

    const violations = await violationsFor(snapshot, "project-manifest");

    expect(violations.map(({ path }) => path)).toEqual([
      "apps/missing",
      "packages/missing",
    ]);
  });

  test("requires docs/audit and rejects docs/audits", async () => {
    const snapshot = await createCleanSnapshot();
    await rm(join(snapshot.root, "docs", "audit"), { recursive: true });
    await mkdir(join(snapshot.root, "docs", "audits"), { recursive: true });

    const violations = await violationsFor(snapshot, "docs-layout");

    expect(violations.map(({ path }) => path)).toEqual([
      "docs/audit",
      "docs/audits",
    ]);
  });

  test("reports only a normalized tracked root debug.log", async () => {
    const snapshot = await createCleanSnapshot();
    const trackedSnapshot = {
      ...snapshot,
      trackedFiles: new Set(["debug.log", "logs/debug.log", "nested\\debug.log"]),
    };

    const violations = await violationsFor(trackedSnapshot, "tracked-files");

    expect(violations).toEqual([
      expect.objectContaining({ path: "debug.log" }),
    ]);
  });

  test("requires a case-insensitive legacy marker in scratchboard README", async () => {
    const missingSnapshot = await createCleanSnapshot();
    await rm(join(missingSnapshot.root, "scratchboard", "README.md"));
    const nonLegacySnapshot = await createCleanSnapshot();
    await write(
      nonLegacySnapshot.root,
      "scratchboard/README.md",
      "# Scratchboard\n\nCurrent project notes.\n",
    );

    expect(await violationsFor(missingSnapshot, "scratchboard")).toHaveLength(1);
    expect(await violationsFor(nonLegacySnapshot, "scratchboard")).toHaveLength(1);
  });
});

describe("report and CLI results", () => {
  test("aggregates and renders all violations in deterministic groups", async () => {
    const snapshot = await createCleanSnapshot();
    await mkdir(join(snapshot.root, "docs", "audits"), { recursive: true });
    await write(snapshot.root, "apps/missing/README.md", "# Missing\n");
    snapshot.trackedFiles = new Set(["debug.log"]);

    const violations = await collectOrganizationViolations(snapshot);
    const report = renderReport(violations);

    expect(violations.map(({ check, path }) => `${check}:${path}`)).toEqual([
      "docs-layout:docs/audits",
      "project-manifest:apps/missing",
      "tracked-files:debug.log",
    ]);
    expect(report).toContain("[docs-layout]");
    expect(report).toContain("[project-manifest]");
    expect(report).toContain("[tracked-files]");
    expect(report).toContain("3 violations");
  });

  test("returns a short success report and exit code zero for a clean tree", async () => {
    const snapshot = await createCleanSnapshot();

    expect(renderReport([])).toBe("Repository organization check passed.");
    expect(
      await runOrganizationCheck({
        cwd: snapshot.root,
        loadSnapshot: async () => snapshot,
      }),
    ).toEqual({
      exitCode: 0,
      output: "Repository organization check passed.",
    });
  });

  test("returns exit code one when organization violations exist", async () => {
    const snapshot = await createCleanSnapshot();
    snapshot.trackedFiles = new Set(["debug.log"]);

    const result = await runOrganizationCheck({
      cwd: snapshot.root,
      loadSnapshot: async () => snapshot,
    });

    expect(result.exitCode).toBe(1);
    expect(result.output).toContain("[tracked-files]");
  });

  test("returns a distinct exit-two result for unexpected filesystem or Git failures", async () => {
    const result = await runOrganizationCheck({
      cwd: "C:/missing",
      loadSnapshot: async () => {
        throw new Error("git unavailable");
      },
    });

    expect(result.exitCode).toBe(2);
    expect(result.output).toBe(
      "Repository organization check failed: git unavailable",
    );
  });
});
