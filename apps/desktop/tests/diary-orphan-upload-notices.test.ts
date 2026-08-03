import { afterEach, describe, expect, test } from "bun:test";
import {
  __resetOrphanUploadNoticesForTest,
  drainOrphanUploadNotices,
  queueOrphanUploadNotice,
  subscribeOrphanUploadNotices,
} from "../src/features/diary/lib/orphanUploadNotices";

afterEach(() => {
  __resetOrphanUploadNoticesForTest();
});

describe("PR #139 round 6 (P2): orphan-upload notices must survive DiaryWorkspace unmounting entirely", () => {
  test("a notice queued while nothing is subscribed (no workspace mounted) is delivered on the next drain", () => {
    // Simulates the user leaving /journal entirely before the upload
    // resolves: nothing is subscribed, so the old setError/reload-based
    // fix could never reach anything. Queuing must not require a live
    // subscriber.
    queueOrphanUploadNotice({ entryId: 1, attachmentId: 42 });

    // Simulates the NEXT DiaryWorkspace mount draining on mount.
    const message = drainOrphanUploadNotices();
    expect(message).not.toBeNull();
    expect(message).toContain("saved as an attachment");
  });

  test("draining is idempotent: a second drain (e.g. a remount) does not re-show an already-shown notice", () => {
    queueOrphanUploadNotice({ entryId: 1, attachmentId: 42 });
    expect(drainOrphanUploadNotices()).not.toBeNull();
    expect(drainOrphanUploadNotices()).toBeNull();
  });

  test("draining with nothing queued returns null", () => {
    expect(drainOrphanUploadNotices()).toBeNull();
  });

  test("several notices queued while unmounted are coalesced into one message with a count, not lost and not shown one-by-one", () => {
    queueOrphanUploadNotice({ entryId: 1, attachmentId: 42 });
    queueOrphanUploadNotice({ entryId: 1, attachmentId: 43 });
    queueOrphanUploadNotice({ entryId: 2, attachmentId: 44 });

    const message = drainOrphanUploadNotices();
    expect(message).toContain("3");
    expect(message).toContain("images");

    // All three were consumed by the one drain — nothing left over.
    expect(drainOrphanUploadNotices()).toBeNull();
  });

  test("a single notice uses singular phrasing, not '1 images'", () => {
    queueOrphanUploadNotice({ entryId: 1, attachmentId: 42 });
    const message = drainOrphanUploadNotices();
    expect(message).not.toContain("1 images");
    expect(message).toContain("An image");
  });

  test("a subscribed listener (workspace still mounted) is notified synchronously when a new notice is queued", () => {
    let notified = 0;
    const unsubscribe = subscribeOrphanUploadNotices(() => {
      notified += 1;
    });

    queueOrphanUploadNotice({ entryId: 1, attachmentId: 42 });
    expect(notified).toBe(1);

    unsubscribe();
    queueOrphanUploadNotice({ entryId: 1, attachmentId: 43 });
    // Unsubscribed — no longer notified, but the notice is still queued
    // for the next drain (it is not lost).
    expect(notified).toBe(1);
    expect(drainOrphanUploadNotices()).not.toBeNull();
  });
});
