import { useEffect } from "react";

/**
 * Report that a dream-bearing greeting is on screen — but only once the page
 * is actually VISIBLE (IL-015 / PR #135 review, P1).
 *
 * A React effect runs after the commit even when the Dashboard mounted in a
 * background or minimized window, so "mounted" is not "seen". Acknowledging
 * there marks the dream surfaced forever; if the window is then closed
 * before it is ever looked at, the dream is consumed unvoiced — the exact
 * loss IL-015 exists to prevent.
 *
 * While the page stays hidden nothing is reported: the claim lapses on its
 * own and the dream is offered again, which is the right failure. If the
 * page becomes visible while the greeting is still rendered, the receipt is
 * reported then.
 */
export function useDreamShownReceipt(shown: boolean, report?: () => void): void {
  useEffect(() => {
    if (!shown || !report) return;
    // No document (SSR/static render): nothing was painted, so nothing is
    // reported — never guess in the direction of consuming a dream.
    if (typeof document === "undefined") return;
    if (document.visibilityState === "visible") {
      report();
      return;
    }
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") report();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () =>
      document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [shown, report]);
}
