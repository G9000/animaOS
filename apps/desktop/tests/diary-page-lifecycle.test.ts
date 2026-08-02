import { describe, expect, test } from "bun:test";
import {
  BLANK_BODY_MARKER,
  hasNonTextNode,
  isDiscardablePage,
  isSignificantEdit,
  resolveBodyForSave,
} from "../src/features/diary/lib/pageLifecycle";

const base = {
  title: null,
  bodyPlainText: "",
  attachmentCount: 0,
  coverAttachmentId: null,
  hasNonTextContent: false,
};

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

  // Task 10 review flagged a data-loss hazard: if bodyPlainText is derived
  // by stripping tags / calling editor.getText(), a page whose ONLY
  // content is a pasted image (or a table, divider, callout, details,
  // task item) strips to "". None of that shows up in attachmentCount or
  // coverAttachmentId (inline images are not upload attachments), so the
  // predicate must not discard when hasNonTextContent is true, even
  // though every other signal says "empty".
  test("keeps a page whose only content is an inline image (bodyPlainText strips to empty)", () => {
    expect(
      isDiscardablePage({
        ...base,
        bodyPlainText: "", // <img> has no text content
        hasNonTextContent: true,
      }),
    ).toBe(false);
  });

  test("keeps a page whose only content is an empty table", () => {
    expect(isDiscardablePage({ ...base, bodyPlainText: "", hasNonTextContent: true })).toBe(false);
  });

  test("keeps a page whose only content is a divider", () => {
    expect(isDiscardablePage({ ...base, bodyPlainText: "", hasNonTextContent: true })).toBe(false);
  });

  test("discards when hasNonTextContent is explicitly false and everything else is empty", () => {
    expect(isDiscardablePage({ ...base, hasNonTextContent: false })).toBe(true);
  });

  // Fix round 1, Finding 2: BLANK_BODY_MARKER is a zero-width space
  // (U+200B), and JS's String.trim() does NOT strip Unicode category Cf
  // "Format" characters — only "White_Space" ones. Before normalizing
  // these out, isDiscardablePage never saw a freshly-created, untouched
  // entry (whose body is exactly BLANK_BODY_MARKER) as discardable, so
  // clicking "+ New entry" and navigating away without typing anything
  // left an Untitled page behind forever.
  test("treats a zero-width-space-only body as empty", () => {
    expect(isDiscardablePage({ ...base, bodyPlainText: BLANK_BODY_MARKER })).toBe(true);
  });

  test("treats a body of only zero-width characters mixed with real whitespace as empty", () => {
    expect(isDiscardablePage({ ...base, bodyPlainText: `  ${BLANK_BODY_MARKER} \n\u200C\u200D\uFEFF ` })).toBe(
      true,
    );
  });

  test("does not treat a zero-width space adjacent to real text as empty", () => {
    expect(isDiscardablePage({ ...base, bodyPlainText: `${BLANK_BODY_MARKER}hello` })).toBe(false);
  });
});

describe("isSignificantEdit", () => {
  // Fix round 1, Finding 1 (CRITICAL): these three cases are exactly what
  // the review asked to be provable in isolation from canSaveDiaryEntry,
  // which was the wrong gate (it treated "editor now empty" as "nothing
  // to save", so an intentional "select all, delete" on a titled entry
  // never scheduled a save and the old body silently resurrected).
  test("untouched freshly-loaded entry -> no save (content matches what was loaded)", () => {
    const html = "<p>secret</p>";
    expect(isSignificantEdit({ loadedHtml: html, currentHtml: html })).toBe(false);
  });

  test("user clears all text -> save (content differs, even though the result is empty)", () => {
    expect(isSignificantEdit({ loadedHtml: "<p>secret</p>", currentHtml: "<p></p>" })).toBe(true);
  });

  test("user types -> save (content differs)", () => {
    expect(isSignificantEdit({ loadedHtml: "<p></p>", currentHtml: "<p>hello</p>" })).toBe(true);
  });
});

describe("resolveBodyForSave", () => {
  test("user types -> saves the actual editor HTML", () => {
    const body = resolveBodyForSave({
      editorIsEmpty: false,
      editorHtml: "<p>hello</p>",
      plainText: "hello",
      attachmentCount: 0,
    });
    expect(body).toBe("<p>hello</p>");
  });

  // The core of Finding 1's fix: clearing all content must produce a save
  // with an explicit blank body, not be skipped.
  test("user clears all text with no attachments -> saves the shared blank marker", () => {
    const body = resolveBodyForSave({
      editorIsEmpty: true,
      editorHtml: "<p></p>",
      plainText: "",
      attachmentCount: 0,
    });
    expect(body).toBe(BLANK_BODY_MARKER);
  });

  test("user clears all text but attachments remain -> keeps the attachment-only label", () => {
    const body = resolveBodyForSave({
      editorIsEmpty: true,
      editorHtml: "<p></p>",
      plainText: "",
      attachmentCount: 2,
    });
    expect(body).toBe("Attachment-only diary entry.");
  });

  // Finding 2's coordination note: a freshly-created entry (before any
  // typing) and an intentionally-cleared entry must agree on one blank
  // representation — both go through this same function.
  test("a brand-new blank entry's resolved body matches what an intentional clear produces", () => {
    const fresh = resolveBodyForSave({
      editorIsEmpty: true,
      editorHtml: "<p></p>",
      plainText: "",
      attachmentCount: 0,
    });
    const cleared = resolveBodyForSave({
      editorIsEmpty: true,
      editorHtml: "<p></p>",
      plainText: "",
      attachmentCount: 0,
    });
    expect(fresh).toBe(cleared);
    expect(fresh).toBe(BLANK_BODY_MARKER);
  });
});

describe("hasNonTextNode", () => {
  test("is false for an empty list", () => {
    expect(hasNonTextNode([])).toBe(false);
  });

  test("is false for plain text-carrying nodes", () => {
    expect(hasNonTextNode(["doc", "paragraph", "text", "heading", "bulletList", "listItem"])).toBe(
      false,
    );
  });

  test("is true when an image node is present", () => {
    expect(hasNonTextNode(["doc", "paragraph", "image"])).toBe(true);
  });

  // Task 13: diaryImage is the new attachment-backed inline image node,
  // registered alongside (not instead of) the legacy base64 "image" node
  // above. Same hazard, same fix: a page whose only content is a
  // newly-pasted attachment-backed image strips to "" plain text and isn't
  // reflected in attachmentCount (useAttachmentUpload deliberately does not
  // mirror into the entries' attachments array), so without this entry the
  // untitled-page cleanup would silently delete it.
  test("is true when a diaryImage node is present", () => {
    expect(hasNonTextNode(["doc", "paragraph", "diaryImage"])).toBe(true);
  });

  test("is true when a table node is present", () => {
    expect(hasNonTextNode(["doc", "table", "tableRow", "tableCell"])).toBe(true);
  });

  test("is true when a horizontalRule (divider) node is present", () => {
    expect(hasNonTextNode(["doc", "paragraph", "horizontalRule"])).toBe(true);
  });

  test("is true when a callout node is present", () => {
    expect(hasNonTextNode(["doc", "callout", "paragraph"])).toBe(true);
  });

  test("is true when a details node is present", () => {
    expect(hasNonTextNode(["doc", "details", "detailsSummary", "detailsContent"])).toBe(true);
  });

  test("is true when a taskItem node is present", () => {
    expect(hasNonTextNode(["doc", "taskList", "taskItem", "paragraph"])).toBe(true);
  });

  // Bundled minor from fix round 1: an empty fenced code block or an empty
  // blockquote is a deliberate block-level formatting choice (same class
  // of hazard as the image/table cases above), and both strip to ""
  // plain text.
  test("is true when a codeBlock node is present (even if empty)", () => {
    expect(hasNonTextNode(["doc", "codeBlock"])).toBe(true);
  });

  test("is true when a blockquote node is present (even if empty)", () => {
    expect(hasNonTextNode(["doc", "blockquote", "paragraph"])).toBe(true);
  });

  // Deliberately NOT non-text: a lone hard break reads as incidental
  // keystroke noise (e.g. a stray Shift+Enter), not a content choice the
  // user made the way inserting a table/callout/code block is.
  test("is false when only a hardBreak node is present", () => {
    expect(hasNonTextNode(["doc", "paragraph", "hardBreak"])).toBe(false);
  });
});
