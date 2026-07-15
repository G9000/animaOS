import { readdir, readFile, stat } from "node:fs/promises";
import { basename, join, resolve } from "node:path";

export const CANONICAL_STATUSES = new Set([
  "backlog",
  "in_progress",
  "blocked",
  "done",
]);

const CHILD_TABLE_HEADINGS = new Set([
  "Child Ticket Order",
  "Child Tickets",
]);
const PROJECT_MANIFESTS = [
  "package.json",
  "project.json",
  "pyproject.toml",
  "Cargo.toml",
];

export type ParentRow = {
  ticketId: string;
  status: string;
};

export type TicketDocument = {
  path: string;
  ticketId?: string;
  title?: string;
  status?: string;
  parent?: string;
  completed?: string;
  parentRows: ParentRow[];
  hasAuthoritativeChildTable: boolean;
};

export type OrganizationViolation = {
  check: string;
  path: string;
  message: string;
};

export type RepositorySnapshot = {
  root: string;
  trackedFiles: ReadonlySet<string>;
};

export type OrganizationCheckResult = {
  exitCode: 0 | 1 | 2;
  output: string;
};

export type OrganizationCheckOptions = {
  cwd?: string;
  loadSnapshot?: (cwd: string) => Promise<RepositorySnapshot>;
};

function compareText(left: string, right: string) {
  return left < right ? -1 : left > right ? 1 : 0;
}

function normalizePath(path: string) {
  return path.replaceAll("\\", "/").replace(/^\.\//, "");
}

function stripInlineCode(value: string) {
  const trimmed = value.trim();
  return trimmed.startsWith("`") && trimmed.endsWith("`")
    ? trimmed.slice(1, -1).trim()
    : trimmed;
}

function tokenizeMarkdownRowCells(row: string) {
  const cells: string[] = [];
  let cell = "";
  let consecutiveBackslashes = 0;

  for (const character of row) {
    if (character === "|" && consecutiveBackslashes % 2 === 0) {
      cells.push(cell);
      cell = "";
      consecutiveBackslashes = 0;
      continue;
    }
    cell += character;
    consecutiveBackslashes =
      character === "\\" ? consecutiveBackslashes + 1 : 0;
  }

  cells.push(cell);
  return cells;
}

function parseMarkdownRow(line: string) {
  const trimmed = line.trim();
  if (!trimmed.startsWith("|")) {
    return undefined;
  }

  const cells = tokenizeMarkdownRowCells(trimmed);
  if (cells[0] === "") {
    cells.shift();
  }
  if (cells.at(-1) === "") {
    cells.pop();
  }
  return cells.map((cell) => stripInlineCode(cell));
}

function isSeparatorRow(cells: string[]) {
  return cells.length > 0 && cells.every((cell) => /^:?-+:?$/.test(cell.trim()));
}

function parseTopMetadata(lines: string[]) {
  const firstSection = lines.findIndex((line) => /^##(?:\s|$)/.test(line));
  const topLines = firstSection === -1 ? lines : lines.slice(0, firstSection);
  const fields = new Map<string, string>();

  for (const line of topLines) {
    const match = line.match(/^-\s+([A-Za-z][A-Za-z ]*):\s*(.*?)\s*$/);
    if (match) {
      fields.set(match[1]!, stripInlineCode(match[2]!));
    }
  }

  return { fields, topLines };
}

export function parseTicketDocument(path: string, text: string): TicketDocument {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const { fields, topLines } = parseTopMetadata(lines);
  let ticketId: string | undefined;
  let title: string | undefined;

  for (const line of topLines) {
    const heading = line.match(/^#\s+([A-Za-z][A-Za-z0-9]*-\d+)(?:\s+-\s+(.+?))?\s*$/);
    if (heading) {
      ticketId = heading[1]!.toUpperCase();
      title = heading[2]?.trim();
      break;
    }
  }

  const parentRows: ParentRow[] = [];
  let hasAuthoritativeChildTable = false;

  for (let index = 0; index < lines.length; index += 1) {
    const heading = lines[index]!.match(/^##\s+(.+?)\s*$/);
    if (!heading || !CHILD_TABLE_HEADINGS.has(heading[1]!)) {
      continue;
    }

    let headerIndex = index + 1;
    let headerWidth = 0;
    let ticketColumn = -1;
    let statusColumn = -1;
    for (; headerIndex < lines.length; headerIndex += 1) {
      if (/^##(?:\s|$)/.test(lines[headerIndex]!)) {
        break;
      }
      const candidate = parseMarkdownRow(lines[headerIndex]!);
      if (!candidate) {
        continue;
      }
      const normalizedHeaders = candidate.map((cell) => cell.toLowerCase());
      ticketColumn = normalizedHeaders.indexOf("ticket");
      statusColumn = normalizedHeaders.indexOf("status");
      if (ticketColumn !== -1 && statusColumn !== -1) {
        headerWidth = candidate.length;
        break;
      }
    }
    if (ticketColumn === -1 || statusColumn === -1) {
      continue;
    }

    const separator = parseMarkdownRow(lines[headerIndex + 1] ?? "");
    if (
      !separator ||
      separator.length !== headerWidth ||
      !isSeparatorRow(separator)
    ) {
      continue;
    }

    hasAuthoritativeChildTable = true;
    let rowIndex = headerIndex + 2;
    for (; rowIndex < lines.length; rowIndex += 1) {
      const cells = parseMarkdownRow(lines[rowIndex]!);
      if (!cells) {
        break;
      }
      if (isSeparatorRow(cells)) {
        continue;
      }

      const rowTicketId = stripInlineCode(cells[ticketColumn] ?? "").toUpperCase();
      if (!rowTicketId) {
        continue;
      }
      parentRows.push({
        ticketId: rowTicketId,
        status: stripInlineCode(cells[statusColumn] ?? ""),
      });
    }
  }

  const parent = fields.get("Parent");
  return {
    path: normalizePath(path),
    ticketId,
    title,
    status: fields.get("Status"),
    parent:
      parent && parent.toLowerCase() !== "none"
        ? parent.toUpperCase()
        : undefined,
    completed: fields.get("Completed"),
    parentRows,
    hasAuthoritativeChildTable,
  };
}

async function pathStat(path: string) {
  try {
    return await stat(path);
  } catch (error) {
    if (
      error &&
      typeof error === "object" &&
      "code" in error &&
      error.code === "ENOENT"
    ) {
      return undefined;
    }
    throw error;
  }
}

async function listMarkdownFiles(root: string, relativeDirectory: string) {
  const paths: string[] = [];

  async function visit(directory: string, relativePath: string) {
    const entries = await readdir(directory, { withFileTypes: true });
    entries.sort((left, right) => compareText(left.name, right.name));
    for (const entry of entries) {
      const entryPath = join(directory, entry.name);
      const entryRelativePath = normalizePath(join(relativePath, entry.name));
      if (entry.isDirectory()) {
        await visit(entryPath, entryRelativePath);
      } else if (entry.isFile() && entry.name.endsWith(".md")) {
        paths.push(entryRelativePath);
      }
    }
  }

  await visit(join(root, relativeDirectory), relativeDirectory);
  return paths;
}

function ticketIdFromFilename(path: string) {
  const match = basename(path).match(/^([A-Za-z][A-Za-z0-9]*-\d+)(?:-|\.|$)/);
  return match?.[1]?.toUpperCase();
}

function addViolation(
  violations: OrganizationViolation[],
  check: string,
  path: string,
  message: string,
) {
  violations.push({ check, path: normalizePath(path), message });
}

async function collectTicketViolations(
  snapshot: RepositorySnapshot,
  violations: OrganizationViolation[],
) {
  const ticketPaths = await listMarkdownFiles(snapshot.root, "tickets");
  const ticketDocuments: TicketDocument[] = [];

  for (const path of ticketPaths) {
    if (basename(path).toLowerCase() === "readme.md" || path === "tickets/TEMPLATE.md") {
      continue;
    }
    const document = parseTicketDocument(
      path,
      await readFile(join(snapshot.root, ...path.split("/")), "utf8"),
    );
    if (ticketIdFromFilename(path) || document.ticketId) {
      ticketDocuments.push(document);
    }
  }

  const ticketsById = new Map<string, TicketDocument[]>();
  for (const document of ticketDocuments) {
    const id = ticketIdFromFilename(document.path) ?? document.ticketId;
    if (!id) {
      continue;
    }
    const matches = ticketsById.get(id) ?? [];
    matches.push(document);
    ticketsById.set(id, matches);
  }

  for (const [ticketId, documents] of ticketsById) {
    if (documents.length > 1) {
      addViolation(
        violations,
        "ticket-links",
        documents[0]!.path,
        `Ticket ID ${ticketId} is ambiguous across ${documents.length} files: ${documents.map(({ path }) => path).join(", ")}.`,
      );
    }
  }

  for (const document of ticketDocuments) {
    if (document.status !== undefined && !CANONICAL_STATUSES.has(document.status)) {
      addViolation(
        violations,
        "ticket-status",
        document.path,
        `Authoritative Status uses noncanonical value "${document.status}".`,
      );
    }
    if (document.completed && document.status !== "done") {
      addViolation(
        violations,
        "ticket-completion",
        document.path,
        `Completed is nonempty but Status is "${document.status ?? "missing"}" instead of "done".`,
      );
    }
    for (const row of document.parentRows) {
      if (!CANONICAL_STATUSES.has(row.status)) {
        addViolation(
          violations,
          "ticket-status",
          document.path,
          `Authoritative child row ${row.ticketId} uses noncanonical status "${row.status}".`,
        );
      }
    }
  }

  const conformingParents = new Map<string, TicketDocument[]>();
  for (const document of ticketDocuments) {
    const id = ticketIdFromFilename(document.path) ?? document.ticketId;
    if (id?.endsWith("-000") && document.hasAuthoritativeChildTable) {
      const matches = conformingParents.get(id) ?? [];
      matches.push(document);
      conformingParents.set(id, matches);
    }
  }

  for (const parent of ticketDocuments.filter(
    (document) => document.hasAuthoritativeChildTable,
  )) {
    const parentId = ticketIdFromFilename(parent.path) ?? parent.ticketId;
    if (!parentId) {
      continue;
    }
    for (const row of parent.parentRows) {
      const children = ticketsById.get(row.ticketId) ?? [];
      if (children.length === 0) {
        addViolation(
          violations,
          "ticket-links",
          parent.path,
          `Parent row ${row.ticketId} has no child ticket file.`,
        );
        continue;
      }
      if (children.length > 1) {
        addViolation(
          violations,
          "ticket-links",
          parent.path,
          `Parent row ${row.ticketId} is ambiguous across ${children.length} child ticket files.`,
        );
        continue;
      }

      const child = children[0]!;
      if (child.parent !== parentId) {
        addViolation(
          violations,
          "ticket-links",
          child.path,
          `Parent row ${row.ticketId} belongs to ${parentId}, but child Parent is "${child.parent ?? "missing"}".`,
        );
      }
      if (child.status !== row.status) {
        addViolation(
          violations,
          "ticket-links",
          parent.path,
          `Parent row ${row.ticketId} status "${row.status}" does not match child status "${child.status ?? "missing"}".`,
        );
      }
    }
  }

  for (const child of ticketDocuments) {
    if (!child.parent) {
      continue;
    }
    const childId = ticketIdFromFilename(child.path) ?? child.ticketId;
    if (!childId) {
      continue;
    }

    const parentDocuments = ticketsById.get(child.parent) ?? [];
    if (parentDocuments.length === 0) {
      addViolation(
        violations,
        "ticket-links",
        child.path,
        `Child Parent ${child.parent} has no parent ticket file.`,
      );
      continue;
    }
    const parents = conformingParents.get(child.parent) ?? [];
    if (parents.length === 0) {
      addViolation(
        violations,
        "ticket-links",
        child.path,
        `Child Parent ${child.parent} does not resolve to a conforming parent table.`,
      );
      continue;
    }
    if (parents.length > 1) {
      addViolation(
        violations,
        "ticket-links",
        child.path,
        `Child Parent ${child.parent} is ambiguous across ${parents.length} conforming parents.`,
      );
      continue;
    }

    const rowCount = parents[0]!.parentRows.filter(
      (row) => row.ticketId === childId,
    ).length;
    if (rowCount === 0) {
      addViolation(
        violations,
        "ticket-links",
        child.path,
        `Child ${childId} references ${child.parent} but appears in zero rows of its authoritative table.`,
      );
    } else if (rowCount !== 1) {
      addViolation(
        violations,
        "ticket-links",
        child.path,
        `Child ${childId} references ${child.parent} but appears in ${rowCount} rows of its authoritative table.`,
      );
    }
  }

  const templatePath = "tickets/TEMPLATE.md";
  const templateAbsolutePath = join(snapshot.root, "tickets", "TEMPLATE.md");
  if (!(await pathStat(templateAbsolutePath))) {
    addViolation(
      violations,
      "ticket-template",
      templatePath,
      "Ticket template is missing.",
    );
  } else {
    const templateLines = (await readFile(templateAbsolutePath, "utf8"))
      .replace(/\r\n?/g, "\n")
      .split("\n");
    const { fields } = parseTopMetadata(templateLines);
    for (const field of ["PRD", "Spec", "Plan"]) {
      if (!fields.has(field)) {
        addViolation(
          violations,
          "ticket-template",
          templatePath,
          `Ticket template must expose a top-level ${field}: field.`,
        );
      }
    }
  }
}

async function collectManifestViolations(
  snapshot: RepositorySnapshot,
  violations: OrganizationViolation[],
) {
  for (const directory of ["apps", "packages"]) {
    const entries = await readdir(join(snapshot.root, directory), {
      withFileTypes: true,
    });
    entries.sort((left, right) => compareText(left.name, right.name));
    for (const entry of entries) {
      if (!entry.isDirectory()) {
        continue;
      }
      const projectPath = join(snapshot.root, directory, entry.name);
      let recognized = false;
      for (const manifest of PROJECT_MANIFESTS) {
        const manifestStat = await pathStat(join(projectPath, manifest));
        if (manifestStat?.isFile()) {
          recognized = true;
          break;
        }
      }
      if (!recognized) {
        addViolation(
          violations,
          "project-manifest",
          `${directory}/${entry.name}`,
          `Direct project directory needs one of: ${PROJECT_MANIFESTS.join(", ")}.`,
        );
      }
    }
  }
}

async function collectDocsViolations(
  snapshot: RepositorySnapshot,
  violations: OrganizationViolation[],
) {
  const audit = await pathStat(join(snapshot.root, "docs", "audit"));
  if (!audit?.isDirectory()) {
    addViolation(
      violations,
      "docs-layout",
      "docs/audit",
      "Canonical audit directory must exist.",
    );
  }

  if (await pathStat(join(snapshot.root, "docs", "audits"))) {
    addViolation(
      violations,
      "docs-layout",
      "docs/audits",
      "Deprecated plural audit path must not exist; use docs/audit.",
    );
  }
}

function collectTrackedFileViolations(
  snapshot: RepositorySnapshot,
  violations: OrganizationViolation[],
) {
  const tracksRootDebugLog = [...snapshot.trackedFiles].some(
    (path) => normalizePath(path) === "debug.log",
  );
  if (tracksRootDebugLog) {
    addViolation(
      violations,
      "tracked-files",
      "debug.log",
      "Root debug.log must not be tracked by Git.",
    );
  }
}

async function collectScratchboardViolations(
  snapshot: RepositorySnapshot,
  violations: OrganizationViolation[],
) {
  const path = "scratchboard/README.md";
  const absolutePath = join(snapshot.root, "scratchboard", "README.md");
  const readmeStat = await pathStat(absolutePath);
  if (!readmeStat?.isFile()) {
    addViolation(
      violations,
      "scratchboard",
      path,
      "Scratchboard README must exist and identify the directory as legacy.",
    );
    return;
  }

  if (!/legacy/i.test(await readFile(absolutePath, "utf8"))) {
    addViolation(
      violations,
      "scratchboard",
      path,
      "Scratchboard README must identify the directory as legacy.",
    );
  }
}

export async function collectOrganizationViolations(
  snapshot: RepositorySnapshot,
): Promise<OrganizationViolation[]> {
  const violations: OrganizationViolation[] = [];
  await collectTicketViolations(snapshot, violations);
  await collectManifestViolations(snapshot, violations);
  await collectDocsViolations(snapshot, violations);
  collectTrackedFileViolations(snapshot, violations);
  await collectScratchboardViolations(snapshot, violations);

  return violations.sort(
    (left, right) =>
      compareText(left.check, right.check) ||
      compareText(left.path, right.path) ||
      compareText(left.message, right.message),
  );
}

export function renderReport(violations: OrganizationViolation[]) {
  if (violations.length === 0) {
    return "Repository organization check passed.";
  }

  const grouped = new Map<string, OrganizationViolation[]>();
  for (const violation of violations) {
    const group = grouped.get(violation.check) ?? [];
    group.push(violation);
    grouped.set(violation.check, group);
  }

  const lines = [
    `Repository organization check found ${violations.length} violations:`,
  ];
  for (const check of [...grouped.keys()].sort(compareText)) {
    lines.push("", `[${check}]`);
    for (const violation of grouped.get(check)!) {
      lines.push(`- ${violation.path}: ${violation.message}`);
    }
  }
  return lines.join("\n");
}

async function runGit(args: string[], cwd: string) {
  const subprocess = Bun.spawn(["git", ...args], {
    cwd,
    stderr: "pipe",
    stdout: "pipe",
  });
  const [exitCode, stdout, stderr] = await Promise.all([
    subprocess.exited,
    new Response(subprocess.stdout).text(),
    new Response(subprocess.stderr).text(),
  ]);
  if (exitCode !== 0) {
    throw new Error(stderr.trim() || `git ${args.join(" ")} exited ${exitCode}`);
  }
  return stdout;
}

export async function loadRepositorySnapshot(cwd: string) {
  const rootOutput = await runGit(["rev-parse", "--show-toplevel"], cwd);
  const root = resolve(rootOutput.trim());
  const trackedOutput = await runGit(["ls-files", "--full-name"], root);
  const trackedFiles = new Set(
    trackedOutput
      .replace(/\r\n?/g, "\n")
      .split("\n")
      .filter(Boolean)
      .map(normalizePath),
  );
  return { root, trackedFiles } satisfies RepositorySnapshot;
}

export async function runOrganizationCheck(
  options: OrganizationCheckOptions = {},
): Promise<OrganizationCheckResult> {
  try {
    const cwd = options.cwd ?? process.cwd();
    const snapshot = await (options.loadSnapshot ?? loadRepositorySnapshot)(cwd);
    const violations = await collectOrganizationViolations(snapshot);
    return {
      exitCode: violations.length === 0 ? 0 : 1,
      output: renderReport(violations),
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      exitCode: 2,
      output: `Repository organization check failed: ${message}`,
    };
  }
}

if (import.meta.main) {
  const result = await runOrganizationCheck();
  const print = result.exitCode === 2 ? console.error : console.log;
  print(result.output);
  process.exitCode = result.exitCode;
}
