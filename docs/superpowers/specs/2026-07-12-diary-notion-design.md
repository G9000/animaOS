# Diary as a Notion-like workspace

**Date:** 2026-07-12  
**Status:** Design approved; ready for implementation planning  
**Area:** `apps/desktop`, `apps/server`

## Goal

Turn the desktop diary at `/journal` into a complete Notion-like writing workspace. The experience should support a persistent page library and nested pages while retaining Anima’s diary-specific date, mood, folder, attachment, and voice-note behavior.

The implementation must preserve all existing diary entries and attachments. Existing diary data is migrated forward rather than discarded or rewritten in place without a recovery path.

## Product shape

The desktop route becomes a three-part workspace:

1. **Library sidebar:** new page, search, favorites, folders/tags, and a collapsible nested page tree.
2. **Editor canvas:** breadcrumbs, page icon/title, page properties, and a structured block editor.
3. **Details drawer:** optional page metadata and actions, closed by default so writing remains central.

The library remains visible while browsing and editing. Empty states always include a direct next action: create a page, clear a filter, or start writing.

## Data model

Add a structured diary model alongside the current entry model. The exact table and ORM names should follow existing server conventions.

### Diary page

- Stable page ID and user ID.
- Nullable parent page ID for nested pages.
- Sibling position that is normalized among pages sharing the same parent.
- Title, icon, cover attachment ID, entry date, mood, folder ID.
- Favorite and archived flags.
- Created/updated timestamps.
- Monotonic revision number for stale-write detection.
- Optional legacy entry ID during migration, unique per page.
- Write-once legacy body HTML/plain text and a nullable `legacyNormalizedAt` timestamp used for migration recovery and audit.

### Diary block

- Stable block ID, user ID, page ID, and nullable parent block ID.
- Explicit sibling order that supports deterministic reordering.
- Block type with an extensible union for paragraph, heading, bulleted list, numbered list, quote, code, divider, image, audio, video, file, nested-page reference, and `legacy-rich-text`.
- Structured JSON content/attributes rather than a single page-wide HTML body.
- Media blocks reference the existing attachment records by stable attachment ID; audio blocks are the storage model for voice notes, while image/video/file blocks preserve their existing attachment kind.
- Created/updated timestamps.

The canonical block payload is intentionally small and JSON-serializable:

```text
Block = {
  id, pageId, parentBlockId?, position,
  type,
  content: {
    text?, marks?: [{type, attrs?}],
    level?, items?: [BlockInline[]],
    attachmentId?, attachmentKind?, caption?,
    pageId?, html?
  }
}
```

Paragraph, heading, quote, and code blocks use `text` plus optional inline marks. Headings use `level` 1–3. List blocks use ordered `items` with inline text/marks. Divider blocks have an empty content object. Image/audio/video/file blocks use an attachment ID, kind, and optional caption. Nested-page blocks use a page ID. Only `legacy-rich-text` uses `html`, and only during migration/normalization. `parentBlockId` and `position` define sibling order; unsupported parent/type combinations are rejected by the API.

The schema should allow future block types without changing the page contract. Blocks are scoped by user and page so authorization never depends on client-supplied ownership alone.

### Diary tag

Reusable user-scoped tags attached to pages through a join table. Tags are separate from folders: folders provide hierarchy, while tags provide cross-cutting retrieval and filtering.

## Migration

Create a forward migration for the new tables and a deterministic backfill:

- Every existing diary entry becomes a diary page.
- Title, date, mood, folder, cover, and attachment relationships are preserved.
- The existing HTML/plain-text body is stored losslessly as an initial `legacy-rich-text` block. Unedited legacy pages render that block through the existing sanitized diary renderer. The first edit runs a deterministic HTML-to-Tiptap normalization helper, replaces the legacy block with standard blocks, and keeps the original body in a migration/audit column until the page save succeeds.
- Existing entry IDs remain addressable during the transition so old links and delete flows do not break.
- The migration is idempotent and records enough linkage to avoid duplicate pages on restart.

No existing diary data is deleted by the migration.

## API boundary

Add authenticated page/block/tag operations under the diary API, keeping current entry routes available during transition. New page tables are the source of truth after backfill; compatibility routes resolve `legacy_entry_id` to a page and translate reads/writes to the page plus its blocks rather than creating a second entry.

- List/search pages with parent, folder, favorite, tag, and archived filters.
- Create, update, archive, restore, favorite, move, and delete pages.
- Read, create, update, reorder, and delete blocks for a page.
- Reorder pages among siblings and move pages between parents.
- Create, rename, delete, and attach/detach tags.
- Return page revision values and reject stale updates with a conflict response.
- Keep attachment upload/download and existing diary deletion semantics intact.

Compatibility rules are explicit: legacy `POST /api/diary` creates a page with a legacy-rich-text block and a legacy mapping in one transaction; legacy list/get/update/delete operate only on mapped pages; an unmapped ID returns `404` and never creates a duplicate. Pages created through the canonical page API are not projected into legacy routes. Legacy delete resolves to the mapped page and applies the page delete rule below.

All operations derive the user from the unlocked session and validate page/block ownership server-side. Batch block updates should be transactional so a reorder cannot leave a partially updated page.

Page nesting and folders are independent: the parent page controls the page tree and breadcrumbs; the folder controls the existing diary filing dimension. Moving a page changes only its parent unless the user explicitly changes its folder. A page with no parent is shown at the workspace root, including when it belongs to a folder. Page reorder writes a sibling position and the server renumbers siblings transactionally.

Archiving hides a page and its descendants from normal views but keeps them attached and restorable from an archived filter. Archive and restore operate on the selected subtree as a unit. Deleting a page permanently removes that page's blocks; child pages are reparented to the deleted page's parent so their content is not lost. Attachments are deleted only when no remaining page references them.

## Editor behavior

The editor should feel like a page-based Notion surface while using existing Anima visual language:

- Slash menu for supported block types.
- Markdown shortcuts for headings, lists, quotes, code, and dividers.
- Inline formatting and links through the existing Tiptap foundation.
- Hover add/drag affordances for block insertion and reordering.
- Image/audio/video attachments and cover selection remain supported.
- Nested pages can be created from the sidebar and referenced from a page as a block.
- Page metadata is editable without leaving the canvas.
- Autosave has explicit `Saving`, `Saved`, and `Needs attention` states.
- Local drafts are keyed per user/page and survive reloads or temporary offline periods.

## Persistence and conflict handling

Page metadata and blocks may save through separate endpoints, but both participate in one page-wide revision. Every page-scoped mutation includes `expectedRevision` and increments the revision transactionally: metadata, block CRUD/reorder, page move/reorder, archive/restore, favorite, tag attach/detach, attachment association/cover changes, compatibility writes, and delete. Raw blob upload is provisional; associating it with a page is the revisioned mutation. The desktop client serializes writes per page, so a metadata save and block save cannot race locally. A stale revision on any page or block write returns `409` with the current revision and a reloadable server snapshot.

Legacy normalization is an atomic page operation: the client sends the normalized block tree and `expectedRevision`; the server writes the blocks, marks `legacyNormalizedAt`, and advances the page revision in one transaction. The write-once legacy body remains available for recovery. If normalization conflicts or fails, the legacy block remains the source of truth and no partial normalized tree is exposed.

If the server rejects a stale revision, the UI stops destructive retrying, preserves the local draft, and offers reload/duplicate-local-copy actions. Network failures keep the local draft and expose retry. Successful saves clear the draft only after the server confirms the write.

## Error and empty states

- No pages: create-page CTA.
- No search/filter results: clear-filter CTA.
- Missing or deleted parent: page appears at the root with a recoverable notice.
- Failed block save: retain local content and show retry without losing focus.
- Failed attachment preview: keep filename/type and show a retry or download action.
- Delete: use the existing confirmation pattern and refresh page/sidebar state after success.

## Testing and verification

### Server

- Migration backfills existing entries once and preserves body/title/date/mood/folder/attachments, including audio/video/file attachment kinds and voice-note blocks.
- Page authorization rejects cross-user page IDs.
- Nested page creation, moving, ordering, and deletion behave deterministically.
- Block CRUD and transactional reorder work for each supported block type.
- Tag attach/detach and filtered search work.
- Stale revision responses are rejected without overwriting newer data.
- Compatibility entry routes resolve to the mapped page and do not create duplicate pages.
- Compatibility reads serialize the canonical page/block tree back to the legacy body shape. Compatibility body updates replace the page's block tree through the same atomic normalization transaction and update the page revision; compatibility deletes use the canonical subtree reparenting rule.
- Existing diary delete and attachment endpoints remain covered.

### Desktop

- Block serialization/deserialization preserves content and order.
- Slash commands and markdown shortcuts produce the intended block structures.
- Autosave/debounce and local draft recovery cover success, reload, offline failure, and conflict states.
- Sidebar filters and nested navigation select the correct page.
- Typecheck/build plus a live `/journal` smoke test confirm the editor is visibly rendered and usable.

## Implementation phases and gates

1. **Schema and migration:** tables, constraints, block contract, attachment references, idempotent backfill. Gate on migration tests and preserved-entry fixtures.
2. **Canonical and compatibility API:** page/block/tag CRUD, search/filter, revision conflicts, and legacy-route adapters. Gate on authorization, compatibility, and transactional reorder tests.
3. **Desktop workspace/editor:** sidebar tree, page navigation, block editing, slash commands, media, tags/favorites, and page actions. Gate on desktop typecheck/build and live `/journal` interaction.
4. **Resilience and cleanup:** local drafts, offline retry, conflict recovery, attachment edge cases, delete/archive verification, and documentation. Gate on focused regression tests plus the full required build/test commands.

Each phase must leave the previous phase usable; the desktop does not switch to the new page source of truth until the migration and compatibility API gates pass.

## Non-goals for this pass

- Real-time multi-user collaboration.
- Public sharing or permissions beyond the current authenticated owner.
- Full Notion database/table/board views.
- Arbitrary third-party embeds.
- Replacing the rest of the desktop navigation or dashboard.

## Acceptance criteria

The diary feels like a complete page workspace rather than a list/detail form: a user can create and organize nested pages, search/filter by folders and tags, favorite pages, edit structured blocks with slash commands, attach media, recover drafts, and delete pages without losing existing diary content.
