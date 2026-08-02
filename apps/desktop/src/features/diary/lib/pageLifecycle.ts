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
export const NON_TEXT_NODE_TYPES: ReadonlySet<string> = new Set([
  "image",
  "table",
  "horizontalRule",
  "callout",
  "details",
  "taskList",
  "taskItem",
]);

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
    input.bodyPlainText.trim() === "" &&
    input.attachmentCount === 0 &&
    input.coverAttachmentId === null &&
    !input.hasNonTextContent
  );
}
