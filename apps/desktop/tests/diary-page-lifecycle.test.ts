import { describe, expect, test } from "bun:test";
import { hasNonTextNode, isDiscardablePage } from "../src/features/diary/lib/pageLifecycle";

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
});
