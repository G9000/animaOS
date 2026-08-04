# Diary Tiptap modernization

**Date:** 2026-08-02
**Status:** Design approved; ready for implementation planning
**Area:** `apps/desktop`

## Goal

Rebuild the desktop diary at `/journal` as a modern three-pane writing workspace with a
properly-architected Tiptap editor. The work is desktop-only: the server contract, the diary
schema, and the API are unchanged.

This is a narrower pass than the approved
[Notion workspace design](2026-07-12-diary-notion-design.md), which introduces a server-side
page/block model. That spec remains valid and unimplemented. Nothing here blocks it: this pass
keeps the existing flat entry model and HTML body as the wire format, so the page/block migration
can land later against a cleaner desktop codebase.

## Current state

`apps/desktop/src/pages/Journal.tsx` is a single 2,199-line component holding the sidebar, both
canvas render paths, the editor, recording, attachments, and all API calls. Tiptap 3.27 is already
present (StarterKit + Image + Placeholder), but the slash menu is hand-rolled: a regex trigger,
manual keydown interception, manually computed popup coordinates, and five synchronised refs.

Two existing behaviors constrain the design:

- **Inline images are base64 data URLs embedded in the entry body** (`Journal.tsx:772`), even
  though an encrypted attachment blob store exists alongside. Pasted screenshots bloat the
  encrypted body.
- **Local drafts are deliberately purged** (`Journal.tsx:887`). Diary content must live in the
  encrypted diary service, never in browser storage. This pass does not relax that.

The server model is flat: `DiaryEntryData` has date, title, body, mood, cover, folder, and
attachments. There is no `favorite` field and no `revision` column.

## Architecture

`pages/Journal.tsx` becomes a thin route rendering `<DiaryWorkspace />`. Everything else moves
into a feature folder, following the existing `features/hud` precedent.

```
apps/desktop/src/features/diary/
  DiaryWorkspace.tsx          # three-pane shell
  editor/
    extensions.ts             # extension set factory; single source of schema truth
    DiaryEditor.tsx           # EditorContent + menus
    SlashMenu.tsx             # Suggestion-based
    BubbleMenu.tsx            # selection toolbar
    BlockDragHandle.tsx       # drag handle + block action menu
    nodes/AttachmentImage.tsx # NodeView: attachmentId -> authed blob URL
    nodes/Callout.tsx         # custom node
  panels/
    LibrarySidebar.tsx        # search, folders, entry list
    DetailsDrawer.tsx         # metadata and actions
    PageHeader.tsx            # cover, title, properties, save status
  hooks/
    useDiaryEntries.ts        # list/create/delete/move, folder state
    useAutosave.ts            # React binding over the scheduler
    useAttachmentUpload.ts    # upload, drag-drop, paste
  lib/
    autosaveScheduler.ts      # framework-free; debounce + coalescing
    sanitize.ts               # moved and widened from pages/journal/html.ts
    snapshot.ts               # moved from pages/journal/content.ts
    speech.ts                 # moved unchanged from pages/journal/speech.ts
```

**Boundary rule:** `editor/` never calls the API; `hooks/` never touch ProseMirror. Attachment
upload crosses that boundary exactly once, through a callback passed into the editor config. This
keeps the editor testable without a server and the hooks testable without a DOM.

The three moved `lib/` files keep their existing tests (`journal-html`, `journal-content`,
`journal-speech`), repointed at the new paths. They act as a regression net through the move.

## Editor

### Extension set

`editor/extensions.ts` exports one factory so the app and the tests build an identical schema.

| Need | Package | Configuration |
|---|---|---|
| Base | `@tiptap/starter-kit` | `codeBlock: false`, headings 1-3 |
| Task lists | `@tiptap/extension-list` | `TaskList` + `TaskItem` only |
| Tables | `@tiptap/extension-table` | with resize handles |
| Toggles | `@tiptap/extension-details` | `Details`, `DetailsSummary`, `DetailsContent` |
| Code | `@tiptap/extension-code-block-lowlight` + `lowlight` | reuses installed `highlight.js` |
| Highlight | `@tiptap/extension-highlight` | fixed tone set, `data-tone` |
| Drag handle | `@tiptap/extension-drag-handle-react` + `@tiptap/extension-node-range` | |
| Slash menu | `@tiptap/suggestion` | `char: "/"` |
| Menus | `@tiptap/react/menus` | `BubbleMenu`, `FloatingMenu` |
| Images | custom `diaryImage` node | see below |
| Callouts | custom node | no official extension exists |

StarterKit v3 already bundles Link, Underline, and list keymaps; they must not be registered
twice. All `@tiptap/*` packages are pinned to a single `^3.29.2` range — mixed minors resolve
duplicate `@tiptap/core` copies and corrupt the schema.

### Slash menu

The regex trigger, manual keyboard handling, and manual positioning are deleted and replaced by
`@tiptap/suggestion` with a React renderer. Keyboard navigation, filtering, and range deletion
come from the plugin.

### Images

A `diaryImage` node stores `attachmentId`, not a URL. Its NodeView resolves the authed blob URL
through the existing download endpoint and revokes the object URL on unmount. Paste and drop
upload first and insert second, showing a placeholder while the upload is in flight; on failure
the placeholder becomes a retry chip rather than disappearing mid-paragraph.

Existing entries keep their base64 `<img src="data:...">` content and continue to render: the
stock `image` node stays registered for reads. There is no migration and no rewrite of existing
encrypted bodies.

### Sanitizer

`lib/sanitize.ts` widens the allowlist to admit the new block types:

- **Tags added:** `table`, `thead`, `tbody`, `tr`, `th`, `td`, `colgroup`, `col`, `details`,
  `summary`, `div`, `span`, `mark`, `u`
- **Attributes added:** `data-type`, `data-checked`, `data-tone`, `data-attachment-id`,
  `colspan`, `rowspan`, `colwidth`
- **Never allowed:** `style`, `input`

Every custom node's serialized form must be representable within this allowlist, since the
sanitizer runs on save. Specifically: `diaryImage` serializes to `img[data-attachment-id]`, and
the callout serializes to `div[data-type="callout"][data-tone]`. An attribute the sanitizer
strips is an attribute the node loses on every save.

Two consequences are deliberate:

- **No text color.** The Color extension writes inline `style`. Allowing `style` for a cosmetic
  feature is not a worthwhile trade. Highlight uses a fixed tone set via `data-tone`, styled in
  CSS.
- **Task checkboxes store no `<input>` element.** Tiptap parses checked state from `data-checked`
  on the `<li>`, so stripping the input is lossless on round-trip.

`ALLOW_DATA_ATTR` stays `false`, with the specific `data-*` attributes named explicitly in
`ALLOWED_ATTR`. That interaction is asserted by test rather than assumed.

### Verified by spike

A headless spike (bun + jsdom + `@tiptap/html`) confirmed the following before planning, rather
than leaving them as assumptions:

- Every block type is **stable** across `editor -> sanitize -> editor -> sanitize`. No content
  drifts or degrades over repeated saves.
- Task items round-trip losslessly with the `<input>` stripped: `data-checked="true"` persists on
  the `<li>`, so checked state survives.
- DOMPurify honors named `data-*` entries in `ALLOWED_ATTR` even with `ALLOW_DATA_ATTR: false`.

It also surfaced one accepted limitation. Tiptap's table always serializes inline
`style="min-width: …"` on the `<table>` and on `<col>` elements, and `resizable: false` does not
suppress it. Since `style` is not allowlisted, **table column widths do not persist across a
save**; table structure, content, `colspan`, and `rowspan` all do, and the result is stable. Column
resizing is therefore a view-only affordance. Allowing `style` to preserve widths is not worth the
sanitizer surface.

## Workspace shell

Three panes: `LibrarySidebar` (search, folders, entry list; collapsible to a rail), the canvas,
and `DetailsDrawer` (closed by default, toggled from the header). The canvas is `PageHeader` —
optional cover, borderless title, properties row of date/mood/folder, and save status — above
`DiaryEditor`.

The read/edit mode toggle and its entire second render path are removed. There is one
always-editable canvas.

**Drawer contents,** matching what the flat entry model actually holds: date picker, mood, folder,
cover set/remove, attachment list with upload, voice-note recording (`speech.ts` moves here
intact), word and character count, created/updated timestamps, delete.

## Page lifecycle and persistence

**Creation.** "New page" POSTs an empty entry dated today, then selects it and focuses the title.
This is required rather than cosmetic: `uploadAttachment(entryId, ...)` needs an entry to exist
before any attachment can be uploaded. To prevent abandoned empties accumulating, a page that is
still untouched — no title, no body, no attachments — is deleted when the user navigates away or
the workspace unmounts.

**Autosave.** Debounced ~800ms after the last keystroke, and flushed on blur, page switch, and
unmount. Writes are serialised per entry: one PATCH in flight at a time with the latest state
queued behind it, so saves cannot land out of order. Status is surfaced as `Saving`, `Saved`, or
`Needs attention` with a retry action.

On failure the content stays in the editor and in memory and a retry is offered. Nothing is
written to browser storage, per the existing encryption policy.

The server has no revision column, so this is last-write-wins. That is correct for a single-user
local application, and the design does not simulate conflict resolution it cannot perform.

Body HTML is sanitized on the way into the editor and again before save.

## Error and empty states

- No entries: create-page CTA.
- No search or filter results: clear-filter CTA.
- Attachment preview failure: keep the filename, offer retry or download.
- Upload failure: the inline placeholder becomes a retry chip.
- Save failure: content retained, retry offered, focus preserved.

## Testing

Desktop tests run under `bun test` with `renderToStaticMarkup`. There is no
`@testing-library/react`, so interactive DOM testing is unavailable; jsdom is available and
already used by the sanitizer tests. The decision is to keep the harness as-is and cover
interaction by live smoke test.

**Unit tested:**

- Sanitizer round-trips: new tags and attributes survive; `style`, `input`, `script`, and
  unlisted `data-*` are stripped; task `data-checked` and table `colspan` survive save-then-load.
- Schema round-trip: HTML to Tiptap document and back is stable for every new block type, run
  headlessly in jsdom. Crucially this runs the output through the sanitizer too, so a node whose
  attributes the allowlist strips fails the test instead of silently losing content in
  production. This is the test that catches a mis-registered extension dropping content on save.
- Autosave: `lib/autosaveScheduler.ts` is framework-free, so debounce, in-flight coalescing,
  flush-on-unmount, and failure/retry are tested with fake timers and a stub save function.
- Pure logic: slash-command filtering, the untitled-page cleanup predicate, and the moved
  `snapshot.ts` helpers.

**Verified live, not unit tested:** drag-handle reordering, bubble-menu positioning and commands,
paste and drop upload. These are confirmed by driving the running app at `/journal`.

**Gates:** `bun test apps/desktop/tests`, `bun run build:desktop`, `bun run lint:desktop`, then
the live pass.

## Implementation phases

1. **Extraction.** Move `Journal.tsx` into `features/diary/` behind the existing behavior, move
   the three `lib/` helpers, repoint their tests. Gate: existing tests pass, app behaves as
   before.
2. **Editor.** Extension set, Suggestion slash menu, bubble menu, drag handle, callout node,
   widened sanitizer. Gate: sanitizer and schema round-trip tests.
3. **Shell.** Three-pane layout, always-editable canvas, page header, details drawer, autosave
   scheduler, page lifecycle. Gate: autosave and lifecycle tests.
4. **Attachments.** `diaryImage` node, upload on paste and drop, retry states. Gate: live smoke
   test of the full editor surface.

Each phase leaves `/journal` usable.

## Non-goals

- Server schema, migration, or API changes of any kind.
- The page/block model, nested pages, tags, or revisions from the 2026-07-12 spec.
- Migrating existing base64 image bodies to attachments.
- Text color, arbitrary embeds, or real-time collaboration.
- Local draft persistence.

## Acceptance criteria

`/journal` opens into a three-pane workspace where a page is always editable and saves itself with
visible status. Slash and bubble menus behave like a modern block editor, blocks can be dragged to
reorder, task lists, tables, toggles, callouts, and highlighted code all round-trip through the
sanitizer without loss, and pasted images upload to the encrypted attachment store instead of
bloating the entry body. Existing entries — including ones containing base64 images — open and
render unchanged.
