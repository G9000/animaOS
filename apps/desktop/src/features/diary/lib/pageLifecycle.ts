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
