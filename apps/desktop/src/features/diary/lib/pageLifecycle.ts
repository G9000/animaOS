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
  // PR #139 round 2, Finding 2 (P1): the entry's current mood. A page with
  // no title, body, attachments, or cover but a deliberately-set mood is
  // still a deliberate, metadata-only entry — not something the user
  // "never touched". Unlike folderId/entryDate below, mood has no ambient
  // default: `createEntry` always creates a page with `mood: null` (see
  // hooks/useDiaryEntries.ts), so a non-empty mood can only ever mean the
  // user typed one into DetailsDrawer. No baseline-comparison is needed —
  // any non-empty value is unambiguously a user action.
  mood: string | null;
  // PR #139 round 2 audit: same class of gap as mood, but folderId is NOT
  // safe to treat the same way mood is. `createEntry({ folderId })` seeds a
  // brand-new page's folderId from whatever folder is active in the
  // sidebar at creation time (see hooks/useDiaryEntries.ts and
  // DiaryWorkspace.tsx's `startNewEntry`) — so a non-null folderId on an
  // otherwise-blank page is frequently just ambient context, not a
  // deliberate choice about THIS page. The only genuine signal is whether
  // the user CHANGED it after that: `initialFolderId` is the folderId
  // this entry had when it became selected in this session (captured once,
  // on entry switch — see DiaryWorkspace.tsx's `initialFolderIdRef`), and
  // `folderId` is its current value. A blank page discards if these still
  // match (nothing was deliberately changed); it stays if they differ (the
  // user explicitly filed it into — or out of — a folder).
  folderId: number | null;
  initialFolderId: number | null;
  // Same reasoning and same technique as folderId: `entryDate` always has
  // a value (creation seeds it with today's date — see
  // hooks/useDiaryEntries.ts's `todayISODate()`), so its mere presence is
  // not a signal. Only a change from the value captured when the entry
  // became selected (`initialEntryDate`, mirroring `initialFolderId`) means
  // the user deliberately picked a different date for a blank page.
  entryDate: string;
  initialEntryDate: string;
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
 *
 * PR #139 round 2, Finding 2 (P1): this used to have no mood input at all,
 * so creating a page, setting only its mood, and navigating away silently
 * deleted it — the mood PATCH succeeded, then this predicate judged the
 * page untouched. mood is now checked the same way title is (empty/null
 * only). folderId and entryDate are also checked, but via a
 * changed-from-baseline comparison rather than a bare presence check —
 * both always carry a value seeded at creation from ambient context (the
 * active folder, today's date), so their mere presence is not a reliable
 * signal of deliberate user action; only a change from that baseline is.
 * See the field-level doc comments on DiscardablePageInput for why each
 * signal is shaped the way it is.
 */
export function isDiscardablePage(input: DiscardablePageInput): boolean {
  return (
    (input.title ?? "").trim() === "" &&
    normalizePlainText(input.bodyPlainText) === "" &&
    input.attachmentCount === 0 &&
    input.coverAttachmentId === null &&
    !input.hasNonTextContent &&
    (input.mood ?? "").trim() === "" &&
    input.folderId === input.initialFolderId &&
    input.entryDate === input.initialEntryDate
  );
}

export interface SessionDiscardEligibilityInput extends DiscardablePageInput {
  // Whether THIS entry is one the current workspace session itself POSTed
  // (via `startNewEntry`) and has not yet graduated out of eligibility
  // (see `graduateSessionEntry` below) — never whether it merely looks
  // blank right now.
  createdThisSession: boolean;
}

/**
 * PR #139 round 3, Finding 2 (P1): `isDiscardablePage` alone judges only
 * the entry's CONTENT — it has no notion of where the entry came from.
 * Before this fix, DiaryWorkspace's untitled-page cleanup called it
 * unconditionally against whatever entry was being left, on every one of
 * its three call sites (unmount, startNewEntry, selectEntry). So a
 * pre-existing entry loaded from the server that simply happened to be
 * blank (no title, body, attachments, mood, or cover — exactly the state
 * an intentionally-cleared entry is left in after fix round 1's Finding 1)
 * was silently and permanently deleted just by being opened and left.
 *
 * The cleanup only ever exists for scratch pages THIS workspace session
 * POSTed via `startNewEntry` and the user abandoned untouched — never for
 * anything the user is reopening. `createdThisSession` is the gate: false
 * unconditionally refuses to discard, regardless of how blank the entry's
 * content looks (see DiaryWorkspace.tsx's `sessionCreatedEntryIdsRef`,
 * which is in-memory only for this mount's lifetime and never persisted
 * to browser storage).
 *
 * Ruling on "created, typed, then cleared, then left" (round 3 review's
 * open question — both keeping and deleting are individually defensible):
 * once a session-created entry has had ANY real, user-driven edit — a
 * significant body edit, a title keystroke, or a drawer field commit
 * (mood/date/folder/cover) — it graduates out of session-eligibility
 * PERMANENTLY for the rest of this session (see `graduateSessionEntry`
 * and its call sites in DiaryWorkspace.tsx), even if the user later clears
 * every field back to exactly the blank state a freshly-created page
 * starts in. This errs toward keeping rather than deleting, consistent
 * with every other fail-safe default in this module (`hasNonTextContent`
 * defaulting to `true`, `snapshotBelongsToEntry` refusing an unmatched or
 * absent tag): "looks blank right now" and "was never a real entry" are
 * NOT the same fact, and conflating them is exactly what caused this
 * round's bug. A page that once held real, saved content is
 * indistinguishable — to the user, and therefore to this code — from any
 * other saved entry that happens to be blank, and none of those are ever
 * auto-deleted.
 */
export function isSessionDiscardable(input: SessionDiscardEligibilityInput): boolean {
  if (!input.createdThisSession) return false;
  return isDiscardablePage(input);
}

/**
 * Removes `entryId` from the in-memory set of entries still eligible for
 * the untitled-page cleanup — see the graduation ruling on
 * `isSessionDiscardable` above. Called from DiaryWorkspace.tsx at every
 * point a session-created entry receives a real, user-driven edit: a
 * significant body edit (`handleEditorChange`), a title keystroke
 * (`handleTitleChange`), or a drawer field commit (`handleDrawerUpdate` —
 * mood, date, folder, cover). A no-op if `entryId` was never in the set
 * (already graduated, or was never session-created to begin with) —
 * `Set.delete` is idempotent, so callers never need to check membership
 * first.
 */
export function graduateSessionEntry(sessionCreatedIds: Set<number>, entryId: number): void {
  sessionCreatedIds.delete(entryId);
}

export interface MoodDraft {
  entryId: number;
  mood: string;
}

/**
 * PR #139 round 3, Finding 1 (P1): the untitled-page cleanup used to read
 * `entry.mood` directly — the last server-COMMITTED value. DetailsDrawer
 * commits mood on a 600ms debounce (or blur, or its own unmount flush —
 * see panels/DetailsDrawer.tsx's `commitMoodValue`), so a user who types a
 * mood and switches entries before any of those fire leaves `entry.mood`
 * still at its old (often null) value at the exact moment
 * `evaluateAndMaybeDiscard` runs — `selectEntry` calls it BEFORE changing
 * selection, and the outgoing keyed DetailsDrawer has not unmounted yet.
 * Fed `entry.mood` directly, the predicate would see an empty mood, judge
 * the page untouched, and (composing with round 3 Finding 2) delete it
 * outright — after which the drawer's own unmount-flush PATCH still
 * fires, targeting an entry that no longer exists.
 *
 * `resolveLiveMood` is the fix: prefer the live, possibly-uncommitted
 * draft DetailsDrawer has reported for THIS entry (DiaryWorkspace.tsx's
 * `liveMoodDraftRef`, updated on every keystroke via the `onMoodDraftChange`
 * prop — never persisted to browser storage), falling back to the
 * server-committed value only when no draft has been recorded for this
 * entry (nothing typed yet, or the only draft on hand belongs to some
 * OTHER entry — the `entryId` tag disambiguates the same way
 * `snapshotBelongsToEntry` does for the body snapshot).
 *
 * A scan of every other field DetailsDrawer can dispatch (entryDate,
 * folderId, cover, attachments) found mood is the ONLY one with a
 * debounced/deferred commit path today — entryDate and folderId call
 * `onUpdate` synchronously from their own onChange, and cover/attachment
 * changes go through immediate, non-debounced calls (see
 * DetailsDrawer.tsx). If a future field grows its own debounce, it needs
 * the same treatment: a live-draft ref reported on every keystroke, read
 * in preference to whatever the server last confirmed.
 */
export function resolveLiveMood(
  draft: MoodDraft | null,
  entryId: number,
  committedMood: string | null,
): string | null {
  if (draft && draft.entryId === entryId) return draft.mood;
  return committedMood;
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
