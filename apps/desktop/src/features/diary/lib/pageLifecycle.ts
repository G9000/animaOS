import { resolveDiaryBody } from "./snapshot";

export interface DiscardablePageInput {
  title: string | null;
  bodyPlainText: string;
  attachmentCount: number;
  coverAttachmentId: number | null;
  // Whether the editor document contains any node whose meaning lives
  // outside plain text (image, table, divider, callout, details, task
  // items — see NON_TEXT_NODE_TYPES). Required, not optional: a page whose
  // only content is e.g. a pasted image strips to an empty
  // `bodyPlainText`, so bodyPlainText alone is not a safe signal that the
  // page is untouched. See the doc comment on isDiscardablePage.
  hasNonTextContent: boolean;
}

// Node type names that carry meaning beyond their plain text. Kept as a
// named export so the caller can walk `editor.state.doc` (via
// `descendants`) and test each node's `type.name` against this set,
// without pageLifecycle.ts itself taking a dependency on Tiptap/ProseMirror
// — the predicate stays framework-free and unit-testable with plain
// string arrays.
//
// codeBlock and blockquote were added in fix round 1 (same class of gap as
// the image/table hazard below: an empty fenced code block or an empty
// blockquote is a deliberate block-level formatting choice the user made,
// not incidental noise, and both strip to "" plain text). hardBreak is
// deliberately NOT included: a lone <br> is not something a user chooses as
// "the entirety of an entry" the way inserting a table/callout/code block
// is — it reads as incidental keystroke noise (e.g. a stray Shift+Enter),
// so a document containing only a hard break is still treated as
// discardable.
// "diaryImage" (Task 13) is the attachment-backed inline image node —
// same hazard as the legacy base64 "image" node it coexists with (see
// editor/extensions.ts and editor/nodes/AttachmentImage.tsx): a page whose
// only content is a newly-pasted attachment-backed image strips to ""
// plain text and is not reflected in attachmentCount (useAttachmentUpload
// deliberately does not mirror into the entries' attachments array — see
// its doc comment), so without this entry the untitled-page cleanup would
// treat such a page as discardable and silently delete it.
export const NON_TEXT_NODE_TYPES: ReadonlySet<string> = new Set([
  "image",
  "diaryImage",
  "table",
  "horizontalRule",
  "callout",
  "details",
  "taskList",
  "taskItem",
  "codeBlock",
  "blockquote",
]);

// The chosen single representation of "this page's body is intentionally
// blank" — used both when a brand-new page is created before any typing
// (startNewEntry) and when the user clears an entry back down to nothing
// with no attachments (resolveBodyForSave below). One zero-width space:
// it satisfies the server's `body: Field(min_length=1, ...)` on both
// create and update (a whitespace-only string collapses to null
// server-side via the `_strip_text` validator and would fail that
// constraint), and it renders as nothing if it's ever momentarily visible.
export const BLANK_BODY_MARKER = "\u200b";

// Zero-width / no-visible-width characters that JavaScript's String.trim()
// does NOT strip (they are Unicode category Cf "Format", not "White_Space",
// so ECMAScript's definition of trimmable whitespace excludes them).
// Fix round 1, Finding 2: without normalizing these out, a page whose body
// is exactly BLANK_BODY_MARKER (U+200B) never reads as empty to
// isDiscardablePage, so "+ New entry" followed by navigating away without
// typing anything left an Untitled page behind forever — Step 5 was
// silently dead for every freshly-created, never-touched page.
// U+200B zero-width space, U+200C zero-width non-joiner, U+200D
// zero-width joiner, U+FEFF byte-order-mark / zero-width no-break space.
const ZERO_WIDTH_PATTERN = /[\u200B\u200C\u200D\uFEFF]/g;

function normalizePlainText(text: string): string {
  return text.replace(ZERO_WIDTH_PATTERN, "").trim();
}

/**
 * Pure helper: does this collection of node type names (e.g. gathered by
 * walking a Tiptap document) include any non-text node? Exported
 * separately from isDiscardablePage so it is directly testable against
 * plain string lists, independent of any editor instance.
 */
export function hasNonTextNode(nodeTypeNames: Iterable<string>): boolean {
  for (const name of nodeTypeNames) {
    if (NON_TEXT_NODE_TYPES.has(name)) return true;
  }
  return false;
}

/**
 * A page the user created but never touched. Deleting these on navigate-away
 * keeps the library free of "Untitled" noise, since creating a page now POSTs
 * immediately (attachment upload requires an entry id).
 *
 * CAUTION (data-loss hazard, flagged in Task 10 review): if the caller
 * derives `bodyPlainText` by stripping tags from the editor's HTML output
 * (or via `editor.getText()`), a page whose ONLY content is an inline
 * image, an empty table, a divider, or an empty callout/details/task item
 * strips to "". None of that is reflected in `attachmentCount` (inline
 * images and table cells are not upload attachments) or
 * `coverAttachmentId`. Without `hasNonTextContent`, this predicate would
 * return true for such a page and the caller would delete real user
 * content. `hasNonTextContent` must be computed by scanning the actual
 * document structure (e.g. `editor.state.doc.descendants`), never derived
 * from `bodyPlainText`.
 */
export function isDiscardablePage(input: DiscardablePageInput): boolean {
  return (
    (input.title ?? "").trim() === "" &&
    normalizePlainText(input.bodyPlainText) === "" &&
    input.attachmentCount === 0 &&
    input.coverAttachmentId === null &&
    !input.hasNonTextContent
  );
}

/**
 * Task 12 review, Finding 2: guards `isDiscardablePage` against being
 * evaluated with a content snapshot that describes a DIFFERENT entry than
 * the one being judged.
 *
 * The caller (DiaryWorkspace's `evaluateAndMaybeDiscard`) captures its
 * content snapshot from DiaryEditor's `create` callback, which — because
 * DiaryEditor mounts with `content` as a construction option rather than
 * an imperative `setContent` call — can be deferred behind a macrotask
 * relative to the entry switch itself (confirmed against the installed
 * @tiptap/react sources). If the user switches away again before that
 * fires, the most recent snapshot the caller holds still describes some
 * OTHER entry. Evaluating THIS entry's discardability against THAT data
 * would be judging the wrong entry's content — e.g. deleting an
 * image-only entry because the stale snapshot happened to read empty.
 *
 * This predicate is intentionally timing-agnostic: it decides purely from
 * the tag written alongside the snapshot data (see
 * DiaryWorkspace.tsx's `lastContentSnapshotRef`), never from when that
 * data was written — so it cannot be defeated by any particular ordering
 * of async callbacks, only by the tag itself being wrong (which the
 * writer, not this reader, is responsible for keeping accurate).
 */
export function snapshotBelongsToEntry(snapshotEntryId: number | null, entryId: number): boolean {
  return snapshotEntryId === entryId;
}

export interface SignificantEditInput {
  // The HTML most recently loaded into (or persisted from) the editor for
  // this entry.
  loadedHtml: string;
  // The editor's current sanitized HTML output.
  currentHtml: string;
}

/**
 * Whether the editor's current output actually differs from what was most
 * recently loaded/persisted — i.e. whether there is a real, user-driven
 * edit here worth scheduling a save for.
 *
 * Fix round 1, Finding 1 (CRITICAL): this used to be answered by
 * `canSaveDiaryEntry`, which returns false for a structurally-empty editor
 * with no attachments. That meant "select all, delete" on a titled entry
 * never scheduled anything (canSaveDiaryEntry saw no content), so the old
 * body silently survived on the server and resurrected on reopen — the
 * user could not erase what they had written. Clearing all content is a
 * legitimate, intentional, saveable edit; the only edit that should be
 * skipped is a no-op where the editor's output exactly matches what it was
 * just loaded with (e.g. the programmatic load itself, as a second line of
 * defense alongside `setContent(html, { emitUpdate: false })`). This
 * function deliberately does not look at emptiness, attachment count, or
 * anything else — only "did the content change".
 */
export function isSignificantEdit(input: SignificantEditInput): boolean {
  return input.currentHtml !== input.loadedHtml;
}

// Existing, pre-Task-11 label for an entry whose text was cleared/never
// written but which is backed by real attachments — distinct from
// BLANK_BODY_MARKER, which represents a page with neither text nor
// attachments (see resolveBodyForSave).
const ATTACHMENT_ONLY_BODY_LABEL = "Attachment-only diary entry.";

export interface ResolveBodyForSaveInput {
  editorIsEmpty: boolean;
  // Already-sanitized editor HTML.
  editorHtml: string;
  plainText: string;
  attachmentCount: number;
}

/**
 * What to send as `body` for an autosave PATCH (or the initial eager
 * create). Delegates to resolveDiaryBody (Task 1) for "the editor has real
 * structural content" — that already correctly preserves an image-only or
 * empty-table document (see the CAUTION on isDiscardablePage) because it
 * keys off `editorIsEmpty`, not plain text. When resolveDiaryBody returns
 * null (editor is structurally empty and has no text), this is where the
 * two "blank" cases are unified per fix round 1's coordination note: an
 * entry with real attachments keeps the existing "Attachment-only diary
 * entry." label, and one with neither text nor attachments — whether
 * freshly created (startNewEntry) or just intentionally cleared by the
 * user — gets the single shared BLANK_BODY_MARKER, so both paths agree on
 * one representation of "blank" rather than each inventing their own.
 */
export function resolveBodyForSave(input: ResolveBodyForSaveInput): string {
  const resolved = resolveDiaryBody({
    editorIsEmpty: input.editorIsEmpty,
    editorHtml: input.editorHtml,
    plainText: input.plainText,
  });
  if (resolved != null) return resolved;
  return input.attachmentCount > 0 ? ATTACHMENT_ONLY_BODY_LABEL : BLANK_BODY_MARKER;
}
