# Diary Tiptap Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the desktop diary at `/journal` as a three-pane workspace with a properly architected Tiptap editor, always-editable autosaving canvas, and attachment-backed inline images.

**Architecture:** `pages/Journal.tsx` (2,199 lines) is decomposed into `apps/desktop/src/features/diary/` with a strict boundary — `editor/` never calls the API, `hooks/` never touch ProseMirror. The server contract, diary schema, and API are untouched; HTML remains the wire format.

**Tech Stack:** React 19, Tiptap 3.29, ProseMirror, Tailwind 4, DOMPurify, `bun test` + jsdom, Vite.

**Spec:** `docs/superpowers/specs/2026-08-02-diary-tiptap-modernization-design.md`

## Global Constraints

- **Desktop only.** No changes to `apps/server`, no migrations, no API or schema changes. Do not modify `packages/api-client`.
- **All `@tiptap/*` packages pinned to `^3.29.2`.** Mixed minors resolve duplicate `@tiptap/core` copies and silently corrupt the schema. When adding any Tiptap package, upgrade the four existing ones in the same commit.
- **Never write diary content to browser storage.** No `localStorage`, no `sessionStorage`, no IndexedDB. Diary content lives only in the encrypted diary service and in memory. The existing cleanup effect that purges legacy draft keys must be preserved.
- **Sanitizer never allows `style` or `input`.** Not for any feature, not temporarily.
- **Every custom node's serialized HTML must survive the sanitizer allowlist.** An attribute the sanitizer strips is an attribute the node loses on every save.
- **Existing entries must keep rendering**, including bodies containing `<img src="data:image/png;base64,…">`. The stock Tiptap `image` node stays registered for reads.
- Run `bun install` from the repo root, not from `apps/desktop`.
- Tests live flat in `apps/desktop/tests/*.test.ts`, run with `bun test apps/desktop/tests`.

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `features/diary/DiaryWorkspace.tsx` | Three-pane shell; owns selected-entry state |
| `features/diary/editor/extensions.ts` | Extension set factory — single source of schema truth |
| `features/diary/editor/DiaryEditor.tsx` | `EditorContent` + menu wiring |
| `features/diary/editor/slashCommands.ts` | Command list + pure filter function |
| `features/diary/editor/SlashMenu.tsx` | Suggestion renderer |
| `features/diary/editor/BubbleMenu.tsx` | Selection toolbar |
| `features/diary/editor/BlockDragHandle.tsx` | Drag handle + block actions |
| `features/diary/editor/nodes/Callout.tsx` | Callout node + NodeView |
| `features/diary/editor/nodes/AttachmentImage.tsx` | `diaryImage` node + NodeView |
| `features/diary/panels/LibrarySidebar.tsx` | Search, folders, entry list |
| `features/diary/panels/PageHeader.tsx` | Cover, title, properties, save status |
| `features/diary/panels/DetailsDrawer.tsx` | Metadata and actions |
| `features/diary/hooks/useDiaryEntries.ts` | Entry/folder data operations |
| `features/diary/hooks/useAutosave.ts` | React binding over the scheduler |
| `features/diary/hooks/useAttachmentUpload.ts` | Upload, drag-drop, paste |
| `features/diary/lib/autosaveScheduler.ts` | Framework-free debounce + coalescing |
| `features/diary/lib/pageLifecycle.ts` | Untitled-page cleanup predicate |

**Moved:** `pages/journal/html.ts` → `features/diary/lib/sanitize.ts`; `pages/journal/content.ts` → `features/diary/lib/snapshot.ts`; `pages/journal/speech.ts` → `features/diary/lib/speech.ts`.

**Modified:** `pages/Journal.tsx` (becomes a thin route), `apps/desktop/package.json`, `apps/desktop/src/index.css` (editor styles).

---

### Task 1: Move the shared helpers into the feature folder

Pure file moves. No behavior changes. The three existing test files become the regression net for everything that follows, so they must pass unchanged apart from import paths.

**Files:**
- Create: `apps/desktop/src/features/diary/lib/sanitize.ts` (from `src/pages/journal/html.ts`)
- Create: `apps/desktop/src/features/diary/lib/snapshot.ts` (from `src/pages/journal/content.ts`)
- Create: `apps/desktop/src/features/diary/lib/speech.ts` (from `src/pages/journal/speech.ts`)
- Delete: `apps/desktop/src/pages/journal/html.ts`, `content.ts`, `speech.ts`
- Modify: `apps/desktop/src/pages/Journal.tsx` (import paths only)
- Test: `apps/desktop/tests/journal-html.test.ts`, `journal-content.test.ts`, `journal-speech.test.ts` (import paths only)

**Interfaces:**
- Consumes: nothing.
- Produces: `createDiaryHtmlSanitizer(root: WindowLike): (html: string) => string` from `lib/sanitize`; `canSaveDiaryEntry(e: DiarySaveEligibility): boolean`, `resolveDiaryBody(s: DiaryEditorSnapshot): string | null`, and the types `DiaryEditorSnapshot`, `DiarySaveEligibility` from `lib/snapshot`. Speech exports keep their current names.

- [ ] **Step 1: Move the three files with git so history follows**

```bash
mkdir -p apps/desktop/src/features/diary/lib
git mv apps/desktop/src/pages/journal/html.ts apps/desktop/src/features/diary/lib/sanitize.ts
git mv apps/desktop/src/pages/journal/content.ts apps/desktop/src/features/diary/lib/snapshot.ts
git mv apps/desktop/src/pages/journal/speech.ts apps/desktop/src/features/diary/lib/speech.ts
rmdir apps/desktop/src/pages/journal 2>/dev/null || true
```

- [ ] **Step 2: Repoint the three test imports**

In `apps/desktop/tests/journal-html.test.ts`:

```ts
import { createDiaryHtmlSanitizer } from "../src/features/diary/lib/sanitize";
```

In `apps/desktop/tests/journal-content.test.ts`:

```ts
import { canSaveDiaryEntry, resolveDiaryBody } from "../src/features/diary/lib/snapshot";
```

In `apps/desktop/tests/journal-speech.test.ts`, replace the `../src/pages/journal/speech` specifier with `../src/features/diary/lib/speech`. Leave every assertion untouched.

- [ ] **Step 3: Repoint the imports in `Journal.tsx`**

Find the three import lines referencing `./journal/html`, `./journal/content`, and `./journal/speech` and point them at `../features/diary/lib/sanitize`, `../features/diary/lib/snapshot`, and `../features/diary/lib/speech`. Change nothing else in the file.

- [ ] **Step 4: Verify the move is behavior-neutral**

Run: `bun test apps/desktop/tests`
Expected: PASS, 111 tests, 0 failures — the same baseline as before the move.

Run: `bun run build:desktop`
Expected: exit 0, no TypeScript errors.

- [ ] **Step 5: Commit**

```bash
git add -A apps/desktop
git commit -m "refactor(diary): move journal helpers into features/diary/lib"
```

---

### Task 2: Reduce Journal.tsx to a route shell

Move the component body verbatim into `DiaryWorkspace.tsx`. This is deliberately a move, not a rewrite — mixing restructuring with behavior change here makes every later bug ambiguous.

**Files:**
- Create: `apps/desktop/src/features/diary/DiaryWorkspace.tsx`
- Modify: `apps/desktop/src/pages/Journal.tsx`

**Interfaces:**
- Consumes: `lib/sanitize`, `lib/snapshot`, `lib/speech` from Task 1.
- Produces: `export default function DiaryWorkspace(): JSX.Element` — takes no props; reads the user from `AuthContext` exactly as `Journal` does today.

- [ ] **Step 1: Create `DiaryWorkspace.tsx` from the existing component**

Move the entire contents of `pages/Journal.tsx` into `features/diary/DiaryWorkspace.tsx`. Rename the default export from `Journal` to `DiaryWorkspace`. Adjust every relative import for the new depth: `../context/AuthContext` becomes `../../context/AuthContext`, `../lib/...` becomes `../../lib/...`, `../components/...` becomes `../../components/...`, and the Task 1 helpers become `./lib/sanitize`, `./lib/snapshot`, `./lib/speech`. Change no logic.

- [ ] **Step 2: Replace `Journal.tsx` with a route shell**

```tsx
import DiaryWorkspace from "../features/diary/DiaryWorkspace";

export default function Journal() {
  return <DiaryWorkspace />;
}
```

- [ ] **Step 3: Verify nothing changed**

Run: `bun test apps/desktop/tests`
Expected: PASS, 111 tests.

Run: `bun run build:desktop`
Expected: exit 0. TypeScript resolving every moved import is the real assertion here.

- [ ] **Step 4: Commit**

```bash
git add -A apps/desktop
git commit -m "refactor(diary): extract DiaryWorkspace from the Journal route"
```

---

### Task 3: Install Tiptap packages and build the extension set

**Files:**
- Modify: `apps/desktop/package.json`
- Create: `apps/desktop/src/features/diary/editor/extensions.ts`
- Test: `apps/desktop/tests/diary-editor-schema.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `createDiaryExtensions(options?: DiaryExtensionOptions): Extensions` from `editor/extensions`, where `interface DiaryExtensionOptions { placeholder?: string }`. Every later task builds its editor from this factory — tests and app must never assemble their own list.

- [ ] **Step 1: Install the packages and align versions**

```bash
bun add --cwd apps/desktop @tiptap/react@^3.29.2 @tiptap/starter-kit@^3.29.2 @tiptap/extensions@^3.29.2 @tiptap/extension-image@^3.29.2 @tiptap/extension-list@^3.29.2 @tiptap/extension-table@^3.29.2 @tiptap/extension-details@^3.29.2 @tiptap/extension-highlight@^3.29.2 @tiptap/extension-code-block-lowlight@^3.29.2 @tiptap/extension-drag-handle-react@^3.29.2 @tiptap/extension-node-range@^3.29.2 @tiptap/suggestion@^3.29.2 @tiptap/html@^3.29.2 @tiptap/core@^3.29.2 @tiptap/pm@^3.29.2 lowlight@^3.3.0
```

The four pre-existing `@tiptap/*` entries are upgraded to `^3.29.2` by the same command. Confirm no `@tiptap/*` entry in `apps/desktop/package.json` is left on `^3.27.1`.

- [ ] **Step 2: Write the failing schema round-trip test**

Create `apps/desktop/tests/diary-editor-schema.test.ts`. The jsdom globals must be installed *before* Tiptap is imported, which is why the imports are dynamic.

```ts
import { describe, expect, test } from "bun:test";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>");
(globalThis as any).window = dom.window;
(globalThis as any).document = dom.window.document;
(globalThis as any).navigator = dom.window.navigator;
(globalThis as any).DOMParser = dom.window.DOMParser;
(globalThis as any).Node = dom.window.Node;
(globalThis as any).HTMLElement = dom.window.HTMLElement;

const { generateHTML, generateJSON } = await import("@tiptap/html");
const { createDiaryExtensions } = await import("../src/features/diary/editor/extensions");

const extensions = createDiaryExtensions();
const roundTrip = (html: string) => generateHTML(generateJSON(html, extensions), extensions);

describe("diary editor schema", () => {
  test("preserves headings, marks and lists", () => {
    const out = roundTrip("<h2>Title</h2><p>hi <strong>there</strong></p><ul><li><p>a</p></li></ul>");
    expect(out).toContain("<h2>Title</h2>");
    expect(out).toContain("<strong>there</strong>");
    expect(out).toContain("<li><p>a</p></li>");
  });

  test("preserves task list checked state", () => {
    const out = roundTrip(
      '<ul data-type="taskList"><li data-checked="true" data-type="taskItem"><p>done</p></li></ul>',
    );
    expect(out).toContain('data-type="taskList"');
    expect(out).toContain('data-checked="true"');
  });

  test("preserves table structure and spans", () => {
    const out = roundTrip(
      '<table><tbody><tr><th colspan="2"><p>h</p></th></tr><tr><td><p>a</p></td><td><p>b</p></td></tr></tbody></table>',
    );
    expect(out).toContain("<table");
    expect(out).toContain('colspan="2"');
  });

  test("preserves details toggles", () => {
    const out = roundTrip(
      '<details><summary>more</summary><div data-type="detailsContent"><p>hidden</p></div></details>',
    );
    expect(out).toContain("<details");
    expect(out).toContain("<summary>more</summary>");
  });

  test("preserves legacy base64 inline images", () => {
    const out = roundTrip('<p><img src="data:image/png;base64,AAAA" alt="memory"></p>');
    expect(out).toContain('src="data:image/png;base64,AAAA"');
    expect(out).toContain('alt="memory"');
  });

  test("preserves highlight tones", () => {
    const out = roundTrip('<p><mark data-tone="amber">warm</mark></p>');
    expect(out).toContain('data-tone="amber"');
  });
});
```

- [ ] **Step 3: Run it and confirm it fails for the right reason**

Run: `bun test apps/desktop/tests/diary-editor-schema.test.ts`
Expected: FAIL — cannot resolve `../src/features/diary/editor/extensions`. If it fails on a jsdom global instead, the globals block was moved below the imports.

- [ ] **Step 4: Write the extension set**

Create `apps/desktop/src/features/diary/editor/extensions.ts`:

```ts
import type { Extensions } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { Placeholder } from "@tiptap/extensions";
import TiptapImage from "@tiptap/extension-image";
import { TaskItem, TaskList } from "@tiptap/extension-list";
import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import { Details, DetailsContent, DetailsSummary } from "@tiptap/extension-details";
import Highlight from "@tiptap/extension-highlight";
import CodeBlockLowlight from "@tiptap/extension-code-block-lowlight";
import { createLowlight, common } from "lowlight";

const lowlight = createLowlight(common);

export interface DiaryExtensionOptions {
  placeholder?: string;
}

export function createDiaryExtensions(options: DiaryExtensionOptions = {}): Extensions {
  return [
    // codeBlock is replaced by the lowlight variant; registering both throws
    // on duplicate node names. Link and Underline already ship in StarterKit.
    StarterKit.configure({
      codeBlock: false,
      heading: { levels: [1, 2, 3] },
    }),
    Placeholder.configure({
      placeholder: options.placeholder ?? "Write, or press / for blocks",
    }),
    TiptapImage.configure({ allowBase64: true }),
    TaskList,
    TaskItem.configure({ nested: true }),
    Table.configure({ resizable: true }),
    TableRow,
    TableHeader,
    TableCell,
    Details,
    DetailsSummary,
    DetailsContent,
    // Tones are carried on data-tone so the sanitizer never needs to allow
    // inline style. multicolor would write style="background-color: …".
    Highlight.extend({
      addAttributes() {
        return {
          tone: {
            default: null,
            parseHTML: (element) => element.getAttribute("data-tone"),
            renderHTML: (attributes) =>
              attributes.tone ? { "data-tone": attributes.tone as string } : {},
          },
        };
      },
    }).configure({ multicolor: false }),
    CodeBlockLowlight.configure({ lowlight }),
  ];
}
```

`allowBase64: true` is required — existing entries contain base64 data URLs and Tiptap drops them otherwise.

- [ ] **Step 5: Run the test to verify it passes**

Run: `bun test apps/desktop/tests/diary-editor-schema.test.ts`
Expected: PASS, 6 tests.

- [ ] **Step 6: Confirm the whole suite and build are still green**

Run: `bun test apps/desktop/tests`
Expected: PASS, 117 tests.

Run: `bun run build:desktop`
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add -A apps/desktop bun.lock
git commit -m "feat(diary): add Tiptap extension set with task lists, tables, toggles and highlight"
```

---

### Task 4: Widen the sanitizer for the new block types

The security-sensitive task. The spike verified this configuration is stable, but the tests must encode it so a future edit can't quietly widen the surface.

**Files:**
- Modify: `apps/desktop/src/features/diary/lib/sanitize.ts`
- Test: `apps/desktop/tests/journal-html.test.ts`

**Interfaces:**
- Consumes: `createDiaryExtensions` (Task 3) in the stability test.
- Produces: `createDiaryHtmlSanitizer` unchanged in signature; only the allowlist widens.

- [ ] **Step 1: Write the failing tests**

Append to `apps/desktop/tests/journal-html.test.ts`:

```ts
describe("diary HTML sanitizer — modern block types", () => {
  test("preserves task lists, tables, toggles and highlight tones", () => {
    const clean = sanitizeDiaryHtml(
      '<ul data-type="taskList"><li data-checked="true" data-type="taskItem"><p>done</p></li></ul>' +
        '<table><colgroup><col></colgroup><tbody><tr><td colspan="2" rowspan="1"><p>a</p></td></tr></tbody></table>' +
        '<details><summary>more</summary><div data-type="detailsContent"><p>hidden</p></div></details>' +
        '<p><mark data-tone="amber">warm</mark></p>' +
        '<p><img data-attachment-id="7" alt="shot"></p>',
    );

    expect(clean).toContain('data-type="taskList"');
    expect(clean).toContain('data-checked="true"');
    expect(clean).toContain('colspan="2"');
    expect(clean).toContain("<details>");
    expect(clean).toContain('data-tone="amber"');
    expect(clean).toContain('data-attachment-id="7"');
  });

  test("never allows style or input through", () => {
    const clean = sanitizeDiaryHtml(
      '<table style="min-width: 50px;"><colgroup><col style="min-width: 25px;"></colgroup>' +
        '<tbody><tr><td><p>a</p></td></tr></tbody></table>' +
        '<ul data-type="taskList"><li data-checked="true"><label><input type="checkbox" checked></label><div><p>x</p></div></li></ul>' +
        '<p style="color: red">red</p>',
    );

    expect(clean).not.toContain("style=");
    expect(clean).not.toContain("<input");
    expect(clean).toContain('data-checked="true"');
  });

  test("strips data attributes that are not explicitly allowlisted", () => {
    const clean = sanitizeDiaryHtml('<p data-evil="1" data-type="callout">x</p>');

    expect(clean).not.toContain("data-evil");
    expect(clean).toContain('data-type="callout"');
  });
});
```

- [ ] **Step 2: Run and confirm failure**

Run: `bun test apps/desktop/tests/journal-html.test.ts`
Expected: FAIL — `data-type`, `colspan`, `<details>` and `<mark>` are stripped by the current allowlist.

- [ ] **Step 3: Widen the allowlist**

In `apps/desktop/src/features/diary/lib/sanitize.ts`, extend `DIARY_HTML_CONFIG`. Add to `ALLOWED_TAGS`: `"col"`, `"colgroup"`, `"details"`, `"div"`, `"mark"`, `"span"`, `"summary"`, `"table"`, `"tbody"`, `"td"`, `"th"`, `"thead"`, `"tr"`, `"u"`. Add to `ALLOWED_ATTR`: `"colspan"`, `"colwidth"`, `"data-attachment-id"`, `"data-checked"`, `"data-tone"`, `"data-type"`, `"rowspan"`.

Leave `ALLOW_ARIA_ATTR: false` and `ALLOW_DATA_ATTR: false` as they are — DOMPurify honors the named `data-*` entries regardless, which the third test pins down. Do not add `style` or `input`.

Add this comment above the config so the constraint survives future edits:

```ts
// `style` and `input` are permanently excluded. Tiptap serializes table
// widths as inline style and task checkboxes as <input>; both are dropped
// deliberately. Checked state survives on the <li> via data-checked, so the
// round-trip is lossless. Column widths do not persist — that is accepted.
```

- [ ] **Step 4: Run to verify it passes**

Run: `bun test apps/desktop/tests/journal-html.test.ts`
Expected: PASS.

- [ ] **Step 5: Add the sanitize/editor stability test**

Append to `apps/desktop/tests/diary-editor-schema.test.ts`:

```ts
const { createDiaryHtmlSanitizer } = await import("../src/features/diary/lib/sanitize");
const sanitize = createDiaryHtmlSanitizer(dom.window as any);

describe("diary editor + sanitizer stability", () => {
  const cases: Record<string, string> = {
    taskList: '<ul data-type="taskList"><li data-checked="true" data-type="taskItem"><p>done</p></li></ul>',
    table: "<table><tbody><tr><td><p>a</p></td><td><p>b</p></td></tr></tbody></table>",
    details: '<details><summary>more</summary><div data-type="detailsContent"><p>hidden</p></div></details>',
    heading: "<h2>Title</h2><p>hi <strong>there</strong></p>",
    legacyImage: '<p><img src="data:image/png;base64,AAAA" alt="memory"></p>',
  };

  for (const [name, input] of Object.entries(cases)) {
    test(`${name} is stable across repeated save cycles`, () => {
      const pass1 = sanitize(roundTrip(input));
      const pass2 = sanitize(roundTrip(pass1));
      expect(pass2).toBe(pass1);
    });
  }
});
```

This is the test that catches content silently degrading over repeated autosaves.

- [ ] **Step 6: Run the full suite**

Run: `bun test apps/desktop/tests`
Expected: PASS, all tests including the 5 new stability cases.

- [ ] **Step 7: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(diary): widen sanitizer for task lists, tables and toggles"
```

---

### Task 5: Replace the hand-rolled slash menu with Suggestion

Deletes the regex trigger, manual keydown interception, manual coordinate math, and five synchronised refs from `DiaryWorkspace.tsx`.

**Files:**
- Create: `apps/desktop/src/features/diary/editor/slashCommands.ts`
- Create: `apps/desktop/src/features/diary/editor/SlashMenu.tsx`
- Modify: `apps/desktop/src/features/diary/editor/extensions.ts`
- Modify: `apps/desktop/src/features/diary/DiaryWorkspace.tsx`
- Test: `apps/desktop/tests/diary-slash-commands.test.ts`

**Interfaces:**
- Consumes: `createDiaryExtensions` (Task 3).
- Produces: from `editor/slashCommands` — `interface SlashCommand { id: string; label: string; hint: string; group: "basic" | "media"; run: (editor: Editor, range: Range) => void }`, `SLASH_COMMANDS: SlashCommand[]`, and `filterSlashCommands(commands: SlashCommand[], query: string): SlashCommand[]`. `createDiaryExtensions` gains `onImageRequest?: () => void` in `DiaryExtensionOptions`, invoked by the Image command instead of running an editor command.

- [ ] **Step 1: Write the failing filter test**

Create `apps/desktop/tests/diary-slash-commands.test.ts`:

```ts
import { describe, expect, test } from "bun:test";
import {
  SLASH_COMMANDS,
  filterSlashCommands,
} from "../src/features/diary/editor/slashCommands";

describe("slash command filtering", () => {
  test("returns every command for an empty query", () => {
    expect(filterSlashCommands(SLASH_COMMANDS, "")).toHaveLength(SLASH_COMMANDS.length);
  });

  test("matches on label case-insensitively", () => {
    const ids = filterSlashCommands(SLASH_COMMANDS, "HEAD").map((c) => c.id);
    expect(ids).toContain("h1");
    expect(ids).not.toContain("divider");
  });

  test("matches on the markdown hint so '1.' finds the numbered list", () => {
    expect(filterSlashCommands(SLASH_COMMANDS, "1.").map((c) => c.id)).toContain("ordered");
  });

  test("returns nothing for an unmatched query", () => {
    expect(filterSlashCommands(SLASH_COMMANDS, "zzzz")).toHaveLength(0);
  });

  test("exposes the block types the diary supports", () => {
    const ids = SLASH_COMMANDS.map((c) => c.id);
    for (const id of ["h1", "h2", "h3", "bullet", "ordered", "task", "quote", "code", "divider", "table", "toggle", "callout", "image"]) {
      expect(ids).toContain(id);
    }
  });
});
```

- [ ] **Step 2: Run and confirm failure**

Run: `bun test apps/desktop/tests/diary-slash-commands.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `slashCommands.ts`**

Move the existing `SLASH_COMMANDS` array out of `DiaryWorkspace.tsx` and extend it. Icons stay as they are — import the existing glyph components rather than inventing new ones.

```ts
import type { Editor, Range } from "@tiptap/core";

export interface SlashCommand {
  id: string;
  label: string;
  hint: string;
  group: "basic" | "media";
  run: (editor: Editor, range: Range) => void;
}

export const SLASH_COMMANDS: SlashCommand[] = [
  { id: "paragraph", label: "Text", hint: "", group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).setParagraph().run() },
  { id: "h1", label: "Heading 1", hint: "#", group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).setNode("heading", { level: 1 }).run() },
  { id: "h2", label: "Heading 2", hint: "##", group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).setNode("heading", { level: 2 }).run() },
  { id: "h3", label: "Heading 3", hint: "###", group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).setNode("heading", { level: 3 }).run() },
  { id: "bullet", label: "Bullet list", hint: "-", group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleBulletList().run() },
  { id: "ordered", label: "Numbered list", hint: "1.", group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleOrderedList().run() },
  { id: "task", label: "To-do list", hint: "[]", group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleTaskList().run() },
  { id: "quote", label: "Quote", hint: ">", group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleBlockquote().run() },
  { id: "code", label: "Code block", hint: "```", group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleCodeBlock().run() },
  { id: "divider", label: "Divider", hint: "---", group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).setHorizontalRule().run() },
  { id: "table", label: "Table", hint: "", group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r)
      .insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run() },
  { id: "toggle", label: "Toggle", hint: "", group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).setDetails().run() },
  { id: "callout", label: "Callout", hint: "", group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).setCallout({ tone: "neutral" }).run() },
  { id: "image", label: "Image", hint: "", group: "media",
    // Handled by the menu renderer, which calls onImageRequest — inserting
    // needs an async upload, so it cannot be a synchronous editor command.
    run: (e, r) => e.chain().focus().deleteRange(r).run() },
];

export function filterSlashCommands(commands: SlashCommand[], query: string): SlashCommand[] {
  const q = query.trim().toLowerCase();
  if (!q) return commands;
  return commands.filter(
    (c) => c.label.toLowerCase().includes(q) || (c.hint !== "" && c.hint.toLowerCase().startsWith(q)),
  );
}
```

`setCallout` arrives in Task 8 and `setDetails` comes from the Details extension. Until Task 8 lands, TypeScript will not know `setCallout` — add the callout entry in Task 8 rather than here if the build fails, and drop `"callout"` from the Step 1 test's id list until then.

- [ ] **Step 4: Run to verify it passes**

Run: `bun test apps/desktop/tests/diary-slash-commands.test.ts`
Expected: PASS, 5 tests.

- [ ] **Step 5: Wire the Suggestion plugin**

Add to `extensions.ts` a `Suggestion`-backed extension. `DiaryExtensionOptions` gains `onImageRequest?: () => void`.

```ts
import { Extension } from "@tiptap/core";
import Suggestion from "@tiptap/suggestion";
import { SLASH_COMMANDS, filterSlashCommands } from "./slashCommands";
import { createSlashRenderer } from "./SlashMenu";

const SlashCommands = Extension.create<{ onImageRequest?: () => void }>({
  name: "diarySlashCommands",
  addOptions() {
    return { onImageRequest: undefined };
  },
  addProseMirrorPlugins() {
    return [
      Suggestion({
        editor: this.editor,
        char: "/",
        startOfLine: false,
        allowSpaces: false,
        items: ({ query }) => filterSlashCommands(SLASH_COMMANDS, query),
        command: ({ editor, range, props }) => {
          if (props.id === "image") {
            editor.chain().focus().deleteRange(range).run();
            this.options.onImageRequest?.();
            return;
          }
          props.run(editor, range);
        },
        render: createSlashRenderer,
      }),
    ];
  },
});
```

Register `SlashCommands.configure({ onImageRequest: options.onImageRequest })` in the array returned by `createDiaryExtensions`.

- [ ] **Step 6: Write the menu renderer**

`SlashMenu.tsx` exports `createSlashRenderer`, returning the object Suggestion expects:

```tsx
import type { SuggestionProps, SuggestionKeyDownProps } from "@tiptap/suggestion";
import type { SlashCommand } from "./slashCommands";

export function createSlashRenderer() {
  let root: HTMLDivElement | null = null;
  let selectedIndex = 0;
  let current: SuggestionProps<SlashCommand> | null = null;

  const render = () => { /* draw items from current.items, marking selectedIndex */ };
  const position = (props: SuggestionProps<SlashCommand>) => {
    const rect = props.clientRect?.();
    if (!rect || !root) return;
    root.style.top = `${rect.bottom + window.scrollY}px`;
    root.style.left = `${rect.left + window.scrollX}px`;
  };

  return {
    onStart(props: SuggestionProps<SlashCommand>) {
      current = props;
      selectedIndex = 0;
      root = document.createElement("div");
      root.className = "diary-slash-menu";
      document.body.appendChild(root);
      render();
      position(props);
    },
    onUpdate(props: SuggestionProps<SlashCommand>) {
      current = props;
      selectedIndex = Math.min(selectedIndex, Math.max(props.items.length - 1, 0));
      render();
      position(props);
    },
    onKeyDown({ event }: SuggestionKeyDownProps) {
      if (!current) return false;
      const count = current.items.length;
      if (event.key === "ArrowDown") {
        selectedIndex = count ? (selectedIndex + 1) % count : 0;
        render();
        return true;
      }
      if (event.key === "ArrowUp") {
        selectedIndex = count ? (selectedIndex - 1 + count) % count : 0;
        render();
        return true;
      }
      if (event.key === "Enter") {
        const item = current.items[selectedIndex];
        if (item) current.command(item);
        return true;
      }
      if (event.key === "Escape") {
        return true;
      }
      return false;
    },
    onExit() {
      root?.remove();
      root = null;
      current = null;
    },
  };
}
```

Reuse the existing menu markup and glyph icon components from `DiaryWorkspace.tsx` inside `render()` so the visual language is unchanged. Style `.diary-slash-menu` in `index.css` with `position: absolute` and the existing popup treatment.

- [ ] **Step 7: Delete the old implementation**

From `DiaryWorkspace.tsx` remove: `SlashRange`, `SlashMenuState`, `SlashCommandItem`, the old `SLASH_COMMANDS` array, `SLASH_TRIGGER_RE`, `syncSlashMenu`, `runSlashCommand`, `filteredSlashCommands`, the `slashMenu` / `slashActiveIndex` state, the four `*Ref` mirrors, the slash branches in the `handleKeyDown` prop, and the inline menu JSX. Build the editor from `createDiaryExtensions({ onImageRequest: … })`.

- [ ] **Step 8: Verify**

Run: `bun test apps/desktop/tests`
Expected: PASS.

Run: `bun run build:desktop`
Expected: exit 0.

- [ ] **Step 9: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(diary): replace hand-rolled slash menu with Tiptap Suggestion"
```

---

### Task 6: Selection bubble menu

**Files:**
- Create: `apps/desktop/src/features/diary/editor/BubbleMenu.tsx`
- Modify: `apps/desktop/src/features/diary/DiaryWorkspace.tsx`

**Interfaces:**
- Consumes: the `Editor` instance.
- Produces: `export function DiaryBubbleMenu({ editor }: { editor: Editor }): JSX.Element | null`.

- [ ] **Step 1: Build the bubble menu**

Import `BubbleMenu` from `@tiptap/react/menus`. Render buttons for bold, italic, strike, inline code, link (prompt for URL, `setLink`/`unsetLink`), and three highlight tones calling `.toggleHighlight({ tone })` with `"amber"`, `"mint"`, `"lilac"`. Each button reflects `editor.isActive(...)`. Hide the menu when the selection is empty or inside a code block:

```tsx
shouldShow={({ editor, from, to }) => from !== to && !editor.isActive("codeBlock")}
```

- [ ] **Step 2: Add the tone styles**

In `apps/desktop/src/index.css`, style `mark[data-tone="amber"]`, `mark[data-tone="mint"]`, and `mark[data-tone="lilac"]` with `background-color` drawn from existing theme tokens via `color-mix`, matching how the rest of the file handles accents. No inline styles — the tone attribute is the only thing persisted.

- [ ] **Step 3: Render it inside the editor**

Mount `<DiaryBubbleMenu editor={editor} />` next to `<EditorContent />` in `DiaryWorkspace.tsx`.

- [ ] **Step 4: Verify**

Run: `bun run build:desktop`
Expected: exit 0.

Run: `bun test apps/desktop/tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(diary): add selection bubble menu with formatting and highlight tones"
```

---

### Task 7: Block drag handle

**Files:**
- Create: `apps/desktop/src/features/diary/editor/BlockDragHandle.tsx`
- Modify: `apps/desktop/src/features/diary/DiaryWorkspace.tsx`, `apps/desktop/src/index.css`

**Interfaces:**
- Consumes: the `Editor` instance.
- Produces: `export function BlockDragHandle({ editor }: { editor: Editor }): JSX.Element | null`.

- [ ] **Step 1: Render the drag handle**

Use `DragHandle` from `@tiptap/extension-drag-handle-react`. Track the hovered node with `onNodeChange({ node, pos })` in component state. Inside the handle render a grip button and a `⋯` button opening a small menu with: **Duplicate** (insert a copy of the node after `pos`), **Delete** (`editor.chain().focus().deleteRange({ from: pos, to: pos + node.nodeSize }).run()`), and **Turn into** (a submenu reusing `SLASH_COMMANDS` filtered to `group === "basic"`, running each command against the node's range).

- [ ] **Step 2: Style the handle**

Add CSS for the handle container: hidden at `opacity: 0`, revealed on hover of the editor, positioned in the left gutter. Give `.ProseMirror` enough left padding that the handle does not overlap text.

- [ ] **Step 3: Mount it**

Render `<BlockDragHandle editor={editor} />` alongside `<EditorContent />`.

- [ ] **Step 4: Verify**

Run: `bun run build:desktop`
Expected: exit 0.

Run: `bun test apps/desktop/tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(diary): add block drag handle with duplicate, delete and turn-into"
```

---

### Task 8: Callout node

**Files:**
- Create: `apps/desktop/src/features/diary/editor/nodes/Callout.tsx`
- Modify: `apps/desktop/src/features/diary/editor/extensions.ts`
- Test: `apps/desktop/tests/diary-editor-schema.test.ts`

**Interfaces:**
- Consumes: `createDiaryExtensions`.
- Produces: `Callout` node named `"callout"` with attribute `tone: "neutral" | "info" | "warn"`, serializing to `div[data-type="callout"][data-tone]`, and command `setCallout(attrs: { tone: string }): boolean`.

- [ ] **Step 1: Write the failing round-trip test**

Append to `apps/desktop/tests/diary-editor-schema.test.ts`:

```ts
describe("callout node", () => {
  test("round-trips through the schema and the sanitizer", () => {
    const input = '<div data-type="callout" data-tone="info"><p>heads up</p></div>';
    const out = roundTrip(input);
    expect(out).toContain('data-type="callout"');
    expect(out).toContain('data-tone="info"');

    const pass1 = sanitize(roundTrip(input));
    const pass2 = sanitize(roundTrip(pass1));
    expect(pass2).toBe(pass1);
    expect(pass1).toContain('data-tone="info"');
  });
});
```

- [ ] **Step 2: Run and confirm failure**

Run: `bun test apps/desktop/tests/diary-editor-schema.test.ts`
Expected: FAIL — the callout `div` is not a known node, so its attributes are lost.

- [ ] **Step 3: Implement the node**

```tsx
import { Node, mergeAttributes } from "@tiptap/core";
import { NodeViewContent, NodeViewWrapper, ReactNodeViewRenderer } from "@tiptap/react";

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    callout: { setCallout: (attrs?: { tone?: string }) => ReturnType };
  }
}

function CalloutView() {
  return (
    <NodeViewWrapper data-type="callout">
      <NodeViewContent />
    </NodeViewWrapper>
  );
}

export const Callout = Node.create({
  name: "callout",
  group: "block",
  content: "block+",
  defining: true,
  addAttributes() {
    return {
      tone: {
        default: "neutral",
        parseHTML: (element) => element.getAttribute("data-tone") ?? "neutral",
        renderHTML: (attributes) => ({ "data-tone": attributes.tone as string }),
      },
    };
  },
  parseHTML() {
    return [{ tag: 'div[data-type="callout"]' }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["div", mergeAttributes(HTMLAttributes, { "data-type": "callout" }), 0];
  },
  addNodeView() {
    return ReactNodeViewRenderer(CalloutView);
  },
  addCommands() {
    return {
      setCallout:
        (attrs = {}) =>
        ({ commands }) =>
          commands.wrapIn(this.name, { tone: attrs.tone ?? "neutral" }),
    };
  },
});
```

Register `Callout` in `createDiaryExtensions`. Add `div[data-type="callout"]` tone styling to `index.css`.

- [ ] **Step 4: Run to verify it passes**

Run: `bun test apps/desktop/tests/diary-editor-schema.test.ts`
Expected: PASS.

- [ ] **Step 5: Re-enable the callout slash command**

If the `callout` entry was omitted from `slashCommands.ts` in Task 5, add it now and restore `"callout"` to the id list in `diary-slash-commands.test.ts`.

Run: `bun test apps/desktop/tests`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(diary): add callout block"
```

---

### Task 9: Autosave scheduler

Framework-free so debounce, coalescing, and failure are testable with fake timers rather than a rendered component.

**Files:**
- Create: `apps/desktop/src/features/diary/lib/autosaveScheduler.ts`
- Test: `apps/desktop/tests/diary-autosave-scheduler.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:

```ts
export type SaveStatus = "idle" | "saving" | "saved" | "error";
export interface AutosaveScheduler<T> {
  schedule(payload: T): void;
  flush(): Promise<void>;
  retry(): Promise<void>;
  status(): SaveStatus;
  dispose(): void;
}
export function createAutosaveScheduler<T>(options: {
  save: (payload: T) => Promise<void>;
  delayMs?: number;
  onStatusChange?: (status: SaveStatus) => void;
}): AutosaveScheduler<T>;
```

- [ ] **Step 1: Write the failing tests**

Create `apps/desktop/tests/diary-autosave-scheduler.test.ts`:

```ts
import { describe, expect, test, mock } from "bun:test";
import { createAutosaveScheduler } from "../src/features/diary/lib/autosaveScheduler";

const tick = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe("autosave scheduler", () => {
  test("debounces rapid edits into a single save with the latest payload", async () => {
    const save = mock(async (_: string) => {});
    const s = createAutosaveScheduler<string>({ save, delayMs: 20 });

    s.schedule("a");
    s.schedule("b");
    s.schedule("c");
    await tick(50);

    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0][0]).toBe("c");
    s.dispose();
  });

  test("never runs two saves concurrently and sends the newest queued payload", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    const seen: string[] = [];
    const save = mock(async (payload: string) => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      seen.push(payload);
      await tick(30);
      inFlight -= 1;
    });
    const s = createAutosaveScheduler<string>({ save, delayMs: 10 });

    s.schedule("first");
    await tick(20);
    s.schedule("second");
    s.schedule("third");
    await tick(100);

    expect(maxInFlight).toBe(1);
    expect(seen).toEqual(["first", "third"]);
    s.dispose();
  });

  test("flush saves immediately without waiting for the debounce", async () => {
    const save = mock(async (_: string) => {});
    const s = createAutosaveScheduler<string>({ save, delayMs: 10_000 });

    s.schedule("urgent");
    await s.flush();

    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0][0]).toBe("urgent");
    s.dispose();
  });

  test("flush is a no-op when there is nothing pending", async () => {
    const save = mock(async (_: string) => {});
    const s = createAutosaveScheduler<string>({ save, delayMs: 10 });

    await s.flush();

    expect(save).toHaveBeenCalledTimes(0);
    s.dispose();
  });

  test("reports error status on failure and retry re-sends the same payload", async () => {
    const statuses: string[] = [];
    let attempt = 0;
    const save = mock(async (_: string) => {
      attempt += 1;
      if (attempt === 1) throw new Error("network");
    });
    const s = createAutosaveScheduler<string>({
      save,
      delayMs: 10,
      onStatusChange: (status) => statuses.push(status),
    });

    s.schedule("keep-me");
    await tick(40);
    expect(s.status()).toBe("error");

    await s.retry();
    expect(s.status()).toBe("saved");
    expect(save.mock.calls[1][0]).toBe("keep-me");
    expect(statuses).toContain("saving");
    expect(statuses).toContain("error");
    s.dispose();
  });

  test("dispose cancels a pending save", async () => {
    const save = mock(async (_: string) => {});
    const s = createAutosaveScheduler<string>({ save, delayMs: 20 });

    s.schedule("dropped");
    s.dispose();
    await tick(50);

    expect(save).toHaveBeenCalledTimes(0);
  });
});
```

- [ ] **Step 2: Run and confirm failure**

Run: `bun test apps/desktop/tests/diary-autosave-scheduler.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the scheduler**

```ts
export type SaveStatus = "idle" | "saving" | "saved" | "error";

export interface AutosaveScheduler<T> {
  schedule(payload: T): void;
  flush(): Promise<void>;
  retry(): Promise<void>;
  status(): SaveStatus;
  dispose(): void;
}

export interface AutosaveSchedulerOptions<T> {
  save: (payload: T) => Promise<void>;
  delayMs?: number;
  onStatusChange?: (status: SaveStatus) => void;
}

export function createAutosaveScheduler<T>(
  options: AutosaveSchedulerOptions<T>,
): AutosaveScheduler<T> {
  const delayMs = options.delayMs ?? 800;

  let timer: ReturnType<typeof setTimeout> | null = null;
  let pending: { payload: T } | null = null;
  let inFlight: Promise<void> | null = null;
  let failed: { payload: T } | null = null;
  let status: SaveStatus = "idle";
  let disposed = false;

  const setStatus = (next: SaveStatus) => {
    if (status === next) return;
    status = next;
    options.onStatusChange?.(next);
  };

  const clearTimer = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  async function run(payload: T): Promise<void> {
    setStatus("saving");
    try {
      await options.save(payload);
      failed = null;
      // A newer edit arrived mid-flight; it is still pending, so do not
      // claim "saved" yet.
      if (pending === null) setStatus("saved");
    } catch {
      failed = { payload };
      setStatus("error");
    }
  }

  async function drain(): Promise<void> {
    if (inFlight) {
      await inFlight;
      return;
    }
    while (pending !== null && !disposed) {
      const next = pending;
      pending = null;
      inFlight = run(next.payload);
      await inFlight;
      inFlight = null;
    }
  }

  return {
    schedule(payload: T) {
      if (disposed) return;
      pending = { payload };
      clearTimer();
      timer = setTimeout(() => {
        timer = null;
        void drain();
      }, delayMs);
    },
    async flush() {
      if (disposed) return;
      clearTimer();
      await drain();
      // A save that finished while another edit was queued leaves work
      // behind; drain again so flush is a real barrier.
      if (pending !== null) await drain();
    },
    async retry() {
      if (disposed || failed === null) return;
      const { payload } = failed;
      failed = null;
      inFlight = run(payload);
      await inFlight;
      inFlight = null;
    },
    status() {
      return status;
    },
    dispose() {
      disposed = true;
      clearTimer();
      pending = null;
    },
  };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `bun test apps/desktop/tests/diary-autosave-scheduler.test.ts`
Expected: PASS, 6 tests. If "never runs two saves concurrently" fails, `drain` is being entered re-entrantly — confirm the `inFlight` guard returns early.

- [ ] **Step 5: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(diary): add framework-free autosave scheduler"
```

---

### Task 10: Page lifecycle rules

**Files:**
- Create: `apps/desktop/src/features/diary/lib/pageLifecycle.ts`
- Test: `apps/desktop/tests/diary-page-lifecycle.test.ts`

**Interfaces:**
- Consumes: `DiaryEntryData` from `@anima/api-client`.
- Produces: `isDiscardablePage(input: DiscardablePageInput): boolean` where `interface DiscardablePageInput { title: string | null; bodyPlainText: string; attachmentCount: number; coverAttachmentId: number | null }`.

- [ ] **Step 1: Write the failing tests**

Create `apps/desktop/tests/diary-page-lifecycle.test.ts`:

```ts
import { describe, expect, test } from "bun:test";
import { isDiscardablePage } from "../src/features/diary/lib/pageLifecycle";

const base = { title: null, bodyPlainText: "", attachmentCount: 0, coverAttachmentId: null };

describe("untitled page cleanup", () => {
  test("discards a page with no title, body, attachments or cover", () => {
    expect(isDiscardablePage(base)).toBe(true);
  });

  test("treats whitespace-only content as empty", () => {
    expect(isDiscardablePage({ ...base, title: "   ", bodyPlainText: "\n \t" })).toBe(true);
  });

  test("keeps a page with a title", () => {
    expect(isDiscardablePage({ ...base, title: "Monday" })).toBe(false);
  });

  test("keeps a page with body text", () => {
    expect(isDiscardablePage({ ...base, bodyPlainText: "hello" })).toBe(false);
  });

  test("keeps a page with an attachment", () => {
    expect(isDiscardablePage({ ...base, attachmentCount: 1 })).toBe(false);
  });

  test("keeps a page with a cover", () => {
    expect(isDiscardablePage({ ...base, coverAttachmentId: 4 })).toBe(false);
  });
});
```

- [ ] **Step 2: Run and confirm failure**

Run: `bun test apps/desktop/tests/diary-page-lifecycle.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```ts
export interface DiscardablePageInput {
  title: string | null;
  bodyPlainText: string;
  attachmentCount: number;
  coverAttachmentId: number | null;
}

/**
 * A page the user created but never touched. Deleting these on navigate-away
 * keeps the library free of "Untitled" noise, since creating a page now POSTs
 * immediately (attachment upload requires an entry id).
 */
export function isDiscardablePage(input: DiscardablePageInput): boolean {
  return (
    (input.title ?? "").trim() === "" &&
    input.bodyPlainText.trim() === "" &&
    input.attachmentCount === 0 &&
    input.coverAttachmentId === null
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `bun test apps/desktop/tests/diary-page-lifecycle.test.ts`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(diary): add untitled page cleanup predicate"
```

---

### Task 11: Always-editable canvas with autosave

Removes the read/edit split. This is the largest behavioral change; the two prior tasks exist so its logic is already tested.

**Files:**
- Create: `apps/desktop/src/features/diary/hooks/useAutosave.ts`
- Create: `apps/desktop/src/features/diary/panels/PageHeader.tsx`
- Modify: `apps/desktop/src/features/diary/DiaryWorkspace.tsx`

**Interfaces:**
- Consumes: `createAutosaveScheduler`, `SaveStatus` (Task 9); `isDiscardablePage` (Task 10); `resolveDiaryBody`, `canSaveDiaryEntry` (Task 1).
- Note: `useDiaryEntries` does not exist yet — it arrives in Task 12. This task calls `api.diary.update` directly from `DiaryWorkspace.tsx`, exactly as the file does today; Task 12 moves those calls behind the hook. Do not create the hook here.
- Produces:
  - `useAutosave<T>(options: { save: (payload: T) => Promise<void>; delayMs?: number }): { schedule: (p: T) => void; flush: () => Promise<void>; retry: () => Promise<void>; status: SaveStatus }`
  - `PageHeader` props: `{ entry: DiaryEntryData; folders: DiaryFolderData[]; saveStatus: SaveStatus; onRetry: () => void; onTitleChange: (title: string) => void; onToggleDrawer: () => void; drawerOpen: boolean }`

- [ ] **Step 1: Write `useAutosave`**

Create the scheduler once in a ref, mirror its status into state via `onStatusChange`, and in the effect cleanup call `flush()` then `dispose()`. Re-create the scheduler when the entry id changes so a queued save can never land on the wrong entry.

- [ ] **Step 2: Delete the read-mode render path**

In `DiaryWorkspace.tsx` remove the `isEditingSelected` state, the `startEditEntry` function, the Edit menu item, the Save button, and the entire `selectedEntry && !isEditingSelected` read-mode branch including its sanitized-HTML rendering. One canvas remains.

- [ ] **Step 3: Wire autosave to the editor**

On `onUpdate`, build the snapshot with the existing helpers and call `schedule({ body, title })`. Sanitize the body before scheduling. Flush on entry switch and on unmount. Load a body into the editor with `setContent(sanitize(entry.body))`, and guard against feeding the editor's own output back in as a change.

- [ ] **Step 4: Build `PageHeader`**

Cover banner (reuse the existing `CoverBanner`), a borderless title input styled as a page title, a properties row of date, mood, and folder, and a save-status chip reading `Saving…`, `Saved`, or `Needs attention` with a Retry button wired to `retry()` when status is `error`. Include the drawer toggle.

- [ ] **Step 5: Apply the untitled-page cleanup**

When the selected entry changes and on unmount, evaluate `isDiscardablePage` against the entry being left; if true, delete it and drop it from the list. Never evaluate it against the entry currently being edited.

- [ ] **Step 6: Verify**

Run: `bun test apps/desktop/tests`
Expected: PASS.

Run: `bun run build:desktop`
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(diary): always-editable canvas with autosave and page header"
```

---

### Task 12: Three-pane shell — sidebar and details drawer

**Files:**
- Create: `apps/desktop/src/features/diary/panels/LibrarySidebar.tsx`
- Create: `apps/desktop/src/features/diary/panels/DetailsDrawer.tsx`
- Create: `apps/desktop/src/features/diary/hooks/useDiaryEntries.ts`
- Modify: `apps/desktop/src/features/diary/DiaryWorkspace.tsx`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `useDiaryEntries(userId: number | null): { entries: DiaryEntryData[]; folders: DiaryFolderData[]; loading: boolean; error: string | null; createEntry: () => Promise<DiaryEntryData | null>; updateEntry: (id: number, data: DiaryEntryUpdateData) => Promise<DiaryEntryData | null>; deleteEntry: (id: number) => Promise<boolean>; reload: () => Promise<void> }`
  - `LibrarySidebar` props: `{ entries; folders; selectedId: number | null; query: string; activeFolderId: number | null; collapsed: boolean; onSelect: (id: number) => void; onQueryChange: (q: string) => void; onFolderChange: (id: number | null) => void; onCreate: () => void; onToggleCollapsed: () => void }`
  - `DetailsDrawer` props: `{ entry: DiaryEntryData; folders: DiaryFolderData[]; open: boolean; onClose: () => void; onUpdate: (data: DiaryEntryUpdateData) => void; onDelete: () => void }`

- [ ] **Step 1: Extract the data layer**

Move every `api.diary.*` call out of `DiaryWorkspace.tsx` into `useDiaryEntries`, keeping the existing error-message strings. The component keeps only selection and UI state.

- [ ] **Step 2: Extract the sidebar**

Move the existing `<aside>` markup into `LibrarySidebar.tsx` unchanged, driven by props. Add a collapse toggle that reduces it to an icon rail. Keep the existing "Load more" paging.

- [ ] **Step 3: Build the details drawer**

A right-hand panel, closed by default, containing: entry date picker, mood control, folder select, cover set/remove, the attachment list with upload, the voice-note recorder (moved from the composer, still using `lib/speech`), word and character counts, created/updated timestamps, and Delete using the existing confirmation pattern.

- [ ] **Step 4: Extract `DiaryEditor.tsx`**

Move the `useEditor` call, `<EditorContent />`, and the menu mounts (`DiaryBubbleMenu`, `BlockDragHandle`) out of `DiaryWorkspace.tsx` into `editor/DiaryEditor.tsx`:

```tsx
export interface DiaryEditorProps {
  entryId: number;
  initialHtml: string;
  onChange: (html: string, plainText: string) => void;
  onImageRequest: () => void;
  onImagePaste: (file: File) => Promise<number | null>;
}

export function DiaryEditor(props: DiaryEditorProps): JSX.Element;
```

Key the component by `entryId` from the parent (`<DiaryEditor key={entry.id} … />`) so switching pages builds a fresh editor rather than mutating one in place. This keeps the boundary rule intact: `DiaryEditor` receives callbacks and never imports the API client.

- [ ] **Step 5: Compose the three panes**

`DiaryWorkspace.tsx` becomes layout plus state: sidebar, canvas (`PageHeader` + `DiaryEditor`), drawer. Target under ~200 lines. Persist the drawer's open state and the sidebar's collapsed state in component state only — never in browser storage.

- [ ] **Step 6: Empty and error states**

No entries → create CTA. No search or filter results → clear-filter CTA. Attachment preview failure → keep the filename and offer retry or download.

- [ ] **Step 7: Verify**

Run: `bun test apps/desktop/tests`
Expected: PASS.

Run: `bun run build:desktop`
Expected: exit 0.

- [ ] **Step 8: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(diary): three-pane workspace with library sidebar and details drawer"
```

---

### Task 13: Attachment-backed inline images

**Files:**
- Create: `apps/desktop/src/features/diary/editor/nodes/AttachmentImage.tsx`
- Create: `apps/desktop/src/features/diary/hooks/useAttachmentUpload.ts`
- Modify: `apps/desktop/src/features/diary/editor/extensions.ts`, `DiaryWorkspace.tsx`
- Test: `apps/desktop/tests/diary-editor-schema.test.ts`

**Interfaces:**
- Consumes: `api.diary.uploadAttachment(entryId, file, caption?)` and `api.diary.downloadAttachment(entryId, attachmentId)` — both already exist; do not change them.
- Produces: `DiaryImage` node named `"diaryImage"`, attributes `attachmentId: number | null`, `caption: string | null`, `uploadState: "uploading" | "ready" | "error"` (**not persisted** — `renderHTML` must never emit it), serializing to `img[data-attachment-id][alt]`.

- [ ] **Step 1: Write the failing round-trip test**

Append to `apps/desktop/tests/diary-editor-schema.test.ts`:

```ts
describe("attachment-backed images", () => {
  test("round-trips attachment id and survives the sanitizer", () => {
    const input = '<img data-attachment-id="42" alt="a shot">';
    const out = roundTrip(input);
    expect(out).toContain('data-attachment-id="42"');

    const pass1 = sanitize(roundTrip(input));
    const pass2 = sanitize(roundTrip(pass1));
    expect(pass2).toBe(pass1);
    expect(pass1).toContain('data-attachment-id="42"');
  });

  test("never persists transient upload state", () => {
    const out = roundTrip('<img data-attachment-id="42" alt="a shot">');
    expect(out).not.toContain("uploadState");
    expect(out).not.toContain("data-upload-state");
  });

  test("still round-trips legacy base64 images alongside attachment images", () => {
    const out = roundTrip(
      '<p><img src="data:image/png;base64,AAAA" alt="old"></p><p><img data-attachment-id="9" alt="new"></p>',
    );
    expect(out).toContain('src="data:image/png;base64,AAAA"');
    expect(out).toContain('data-attachment-id="9"');
  });
});
```

- [ ] **Step 2: Run and confirm failure**

Run: `bun test apps/desktop/tests/diary-editor-schema.test.ts`
Expected: FAIL — `data-attachment-id` is dropped; the stock image node keeps only `src`/`alt`.

- [ ] **Step 3: Implement the node**

A `diaryImage` node with `parseHTML` matching `img[data-attachment-id]` — more specific than the stock image node, so legacy base64 images still parse as `image` and both coexist. The attribute block is the subtle part, because `uploadState` must never reach the saved HTML:

```ts
addAttributes() {
  return {
    attachmentId: {
      default: null,
      parseHTML: (element) => {
        const raw = element.getAttribute("data-attachment-id");
        return raw === null ? null : Number(raw);
      },
      renderHTML: (attributes) =>
        attributes.attachmentId === null
          ? {}
          : { "data-attachment-id": String(attributes.attachmentId) },
    },
    caption: {
      default: null,
      parseHTML: (element) => element.getAttribute("alt"),
      renderHTML: (attributes) =>
        attributes.caption ? { alt: attributes.caption as string } : {},
    },
    // Transient UI state. rendered:false keeps it out of getHTML() entirely,
    // so an in-flight upload can never be persisted into an entry body.
    uploadState: { default: "ready", rendered: false },
  };
},
parseHTML() {
  return [{ tag: "img[data-attachment-id]" }];
},
renderHTML({ HTMLAttributes }) {
  return ["img", mergeAttributes(HTMLAttributes)];
},
``` The NodeView resolves the blob via `downloadAttachment`, holds the object URL in state, and revokes it on unmount, mirroring the existing `useAttachmentBlobUrl` pattern. While `uploadState === "uploading"` it renders a skeleton; on `"error"` it renders a retry chip.

- [ ] **Step 4: Implement the upload hook**

`useAttachmentUpload(entryId: number | null)` exposes `uploadImage(file: File): Promise<number | null>`, returning the new attachment id. It calls `api.diary.uploadAttachment`, surfaces failures through the existing error channel, and returns `null` when `entryId` is null.

- [ ] **Step 5: Wire paste and drop**

In the editor's `handlePaste` and `handleDrop`, intercept image files: insert a `diaryImage` node with `uploadState: "uploading"`, await the upload, then set `attachmentId` and `uploadState: "ready"` on that node by position. On failure set `"error"`. Remove the old `fileToDataUrl` insertion path so no new base64 ever enters a body. Keep the existing markdown-detection paste branch untouched.

- [ ] **Step 6: Verify**

Run: `bun test apps/desktop/tests`
Expected: PASS.

Run: `bun run build:desktop`
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(diary): route inline images through the encrypted attachment store"
```

---

### Task 14: Full verification and live smoke test

**Files:** none created; fixes only.

- [ ] **Step 1: Run the full gates**

```bash
bun test apps/desktop/tests
bun run build:desktop
bun run lint:desktop
```

Expected: all exit 0. Fix anything that fails before continuing — do not proceed to the smoke test on a red build.

- [ ] **Step 2: Start the dev server**

Run `bun run dev:web` and open `/journal`.

- [ ] **Step 3: Walk the checklist**

Confirm each of these by interacting with the running app, not by reading code:

- [ ] An existing entry opens and renders, including any legacy base64 image.
- [ ] Typing shows `Saving…` then `Saved`; reloading the page shows the persisted text.
- [ ] `/` opens the slash menu; arrow keys and Enter insert the right block; Escape closes it.
- [ ] Heading, to-do, table, toggle, callout, and code block all insert and render.
- [ ] A to-do checkbox survives a reload with its checked state intact.
- [ ] Selecting text shows the bubble menu; bold, link, and a highlight tone apply.
- [ ] The drag handle appears on hover and reorders a block; the `⋯` menu duplicates and deletes.
- [ ] Pasting an image uploads it and renders it; the entry body contains no base64.
- [ ] The details drawer opens and edits date, mood, and folder.
- [ ] "New page" then navigating away without typing leaves no `Untitled` entry behind.
- [ ] Collapsing the sidebar and reopening a page preserves the content.

- [ ] **Step 4: Confirm no base64 crept back in**

With an entry containing a pasted image selected, check the network payload of the PATCH request: the body must contain `data-attachment-id` and must not contain `base64`.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A apps/desktop
git commit -m "fix(diary): address issues found in live verification"
```

- [ ] **Step 6: Push and open the PR**

```bash
git push -u origin worktree-diary-tiptap-modernization
```

Open a PR against `main` summarizing the work and listing the accepted limitations: no persisted table column widths, no text color, last-write-wins saves, and legacy base64 images left unmigrated.

---

## Verification Summary

| Gate | Command |
|---|---|
| Unit tests | `bun test apps/desktop/tests` |
| Typecheck + build | `bun run build:desktop` |
| Lint | `bun run lint:desktop` |
| Live | `bun run dev:web`, Task 14 checklist |
