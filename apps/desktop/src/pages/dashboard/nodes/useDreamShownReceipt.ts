import { useEffect } from "react";

/**
 * Report that a dream-bearing greeting is on screen (IL-015 / PR #135
 * review, P1).
 *
 * Two things have to be true before a dream may be acknowledged, because an
 * acknowledgement marks it surfaced forever: it must have been RENDERED
 * (this effect runs after the commit that put the text on screen, and the
 * node calling it can be closed by the user), and the page must actually be
 * VISIBLE — a React effect runs even when the Dashboard mounted in a
 * background window, and consuming a dream nobody could see is the exact
 * loss IL-015 exists to prevent.
 *
 * The Dashboard already withholds the dream itself while the page is hidden
 * or the claim has lapsed, and re-confirms on reveal; the visibility check
 * here is the second, independent guard on the same rule. Nothing is
 * deferred or queued: if the page is not visible, no receipt is owed, and
 * the claim simply lapses so the dream is offered again.
 */
export function useDreamShownReceipt(shown: boolean, report?: () => void): void {
  useEffect(() => {
    if (!shown || !report) return;
    // No document (static render): nothing was painted, so nothing is
    // reported — never guess in the direction of consuming a dream.
    if (typeof document === "undefined") return;
    if (document.visibilityState !== "visible") return;
    report();
  }, [shown, report]);
}
