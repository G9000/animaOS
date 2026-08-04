import { describe, expect, test } from "bun:test";
import { isDiscardablePage, snapshotBelongsToEntry } from "../src/features/diary/lib/pageLifecycle";

const base = {
  title: null,
  bodyPlainText: "",
  attachmentCount: 0,
  coverAttachmentId: null,
};

// Task 12 review, Finding 1b: a missing editor reference must never make a
// page look discardable. "Unavailable" has to fail toward "keep the page",
// never toward "looks discardable".
describe("hasNonTextContent fail-safe default (Task 12 review Finding 1b)", () => {
  test("BEFORE (bug reproduction): defaulting the fallback to false wrongly discards a page whose real content couldn't be inspected", () => {
    // This is exactly the reviewer's scenario: entry holds only an image
    // (so bodyPlainText strips to ""), no title, no attachments, no cover
    // — but the editor reference was unavailable when the snapshot was
    // captured, so the pre-fix code guessed `hasNonTextContent: false`.
    const staleFallbackSnapshot = { ...base, hasNonTextContent: false };
    expect(isDiscardablePage(staleFallbackSnapshot)).toBe(true); // BUG: would delete real content
  });

  test("AFTER (fixed): defaulting the fallback to true keeps the same page", () => {
    const failSafeSnapshot = { ...base, hasNonTextContent: true };
    expect(isDiscardablePage(failSafeSnapshot)).toBe(false); // kept
  });
});

// Task 12 review, Finding 2: `evaluateAndMaybeDiscard` must refuse to
// evaluate an entry's discardability against a content snapshot that is
// tagged as belonging to a DIFFERENT entry (DiaryEditor's `create` — and
// therefore the snapshot's first real population for a newly selected
// entry — can be deferred behind a macrotask relative to the switch
// itself; confirmed against the installed @tiptap/react sources).
describe("snapshotBelongsToEntry (Task 12 review Finding 2)", () => {
  test("refuses a snapshot tagged for a different entry", () => {
    expect(snapshotBelongsToEntry(1, 2)).toBe(false);
  });

  test("accepts a snapshot tagged for the same entry", () => {
    expect(snapshotBelongsToEntry(2, 2)).toBe(true);
  });

  test("refuses when no snapshot has been captured for any entry yet (null tag)", () => {
    expect(snapshotBelongsToEntry(null, 2)).toBe(false);
  });

  test("BEFORE/AFTER: an entry-id mismatch must gate isDiscardablePage, not just feed it stale data", () => {
    // Entry 2 genuinely holds an image (hasNonTextContent: true) and would
    // correctly be kept even if evaluated directly...
    const entry2RealSnapshot = { entryId: 2, bodyPlainText: "", hasNonTextContent: true };
    expect(isDiscardablePage({ ...base, hasNonTextContent: entry2RealSnapshot.hasNonTextContent })).toBe(
      false,
    );

    // ...but the reviewer's race is: the user switches to entry 2 and back
    // away again before entry 2's own editor ever reports in, so the only
    // snapshot on hand is still entry 1's (empty, plain-text-only, no
    // non-text content — genuinely discardable AS ENTRY 1). Evaluating
    // entry 2 against entry 1's data would be judging the wrong entry.
    const entry1StaleSnapshot = { entryId: 1, bodyPlainText: "", hasNonTextContent: false };
    // BEFORE (bug): no entry-id check existed, so this stale-but-plausible
    // snapshot would have been fed straight into isDiscardablePage and
    // entry 2 would be wrongly deleted:
    expect(
      isDiscardablePage({ ...base, hasNonTextContent: entry1StaleSnapshot.hasNonTextContent }),
    ).toBe(true); // BUG, if reached: deletes entry 2 based on entry 1's data

    // AFTER (fixed): the caller checks snapshotBelongsToEntry FIRST, and
    // refuses to reach isDiscardablePage at all when the tags don't match.
    expect(snapshotBelongsToEntry(entry1StaleSnapshot.entryId, 2)).toBe(false);
  });
});
