import { useEffect, useRef } from "react";

/**
 * Report that a dream-bearing greeting is on screen (IL-015 / PR #135
 * review, P1). Returns a ref the node must attach to the element that
 * actually renders the greeting.
 *
 * An acknowledgement marks a dream surfaced forever, so it may only be sent
 * for a dream the user could really see. Three things have to hold, and each
 * one has already been shown to leak a repeat on its own:
 *
 * - RENDERED: this effect runs after the commit that put the text on screen,
 *   and the node calling it can be closed by the user.
 * - The page is VISIBLE: effects run even when the Dashboard mounted in a
 *   background window.
 * - The element is ON SCREEN: the dashboard is a pannable canvas, so a
 *   mounted node can sit well outside the viewport. Page visibility is not
 *   element visibility, so intersection is observed directly.
 * - UNOBSCURED: an observer cannot see occlusion, and the app renders
 *   fixed-position surfaces above the canvas — the gallery lightbox, and an
 *   initiative card owned by the Layout, not the Dashboard. Rather than
 *   enumerate them, the element the ref is attached to is hit-tested at
 *   three points spanning its own height. All three must lie inside the
 *   viewport and resolve to the greeting: a dream sentence sitting just
 *   below the fold is not being read, and anything stacked on top means the
 *   text is not being read either. The ref belongs on the element that renders the
 *   greeting TEXT, not the whole card: an overlay can cover the sentence
 *   while the card's centre stays clear.
 *
 * Occlusion is re-checked on a slow timer while a receipt is still owed,
 * because nothing re-renders when a Layout-owned overlay is dismissed —
 * without it, a greeting that happened to be covered when it first appeared
 * would never be acknowledged at all, which is the harmful direction: the
 * claim lapses and the narrative can be disclosed again.
 *
 * Nothing is queued or deferred. If the greeting is not really visible no
 * receipt is owed, the claim simply lapses, and the dream is offered again —
 * the correct failure for this feature.
 */
export function useDreamShownReceipt<T extends HTMLElement = HTMLElement>(
  shown: boolean,
  report?: () => void,
): React.RefObject<T | null> {
  const ref = useRef<T | null>(null);

  useEffect(() => {
    if (!shown || !report) return;
    // No document (static render): nothing was painted, so nothing is
    // reported — never guess in the direction of consuming a dream.
    if (typeof document === "undefined") return;
    const element = ref.current;
    if (!element) return;

    let reported = false;
    let recheck: ReturnType<typeof setInterval> | undefined;
    const stopRechecking = () => {
      if (recheck !== undefined) clearInterval(recheck);
      recheck = undefined;
    };

    const isOnTop = () => {
      // No geometry (jsdom, display:none): fall back to the coarser checks
      // rather than never acknowledging anything.
      const box = element.getBoundingClientRect?.();
      if (!box || box.width === 0 || box.height === 0) return true;
      if (typeof document.elementFromPoint !== "function") return true;
      const x = box.left + box.width / 2;
      const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
      const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
      if (x < 0 || x > viewportWidth) return false;
      // Sample down the text's own height: a corner overlay can cover the
      // last line of a greeting while leaving its middle clear.
      for (const fraction of [0.05, 0.5, 0.95]) {
        const y = box.top + box.height * fraction;
        // EVERY sample must be on screen, top to bottom (PR #135 review).
        // The dream sentence is appended to the greeting, so accepting a
        // partially visible paragraph would acknowledge a dream whose own
        // sentence was still below the edge. Requiring the whole text block
        // covers that without having to locate the sentence inside the
        // string — which the static-greeting path does not append at the
        // end anyway.
        if (y < 0 || y > viewportHeight) return false;
        const topmost = document.elementFromPoint(x, y);
        // Our own subtree, or a container that wraps it (the canvas pane can
        // swallow the hit): not occluded. Anything else — a lightbox, the
        // initiative card — is stacked on top, so the text is not readable.
        // Null inside the viewport means something non-elemental is there;
        // treat it as covered. The recheck timer below makes a wrong "no"
        // recoverable, whereas a wrong "yes" consumes the dream for good.
        if (topmost === null) return false;
        if (!element.contains(topmost) && !topmost.contains(element)) return false;
      }
      return true;
    };

    const reportOnce = () => {
      if (reported || document.visibilityState !== "visible") return false;
      if (!isOnTop()) return false;
      reported = true;
      stopRechecking();
      report();
      return true;
    };

    // Covered right now? Keep looking. Dismissing a Layout-owned overlay
    // changes neither the intersection nor this node's props, so nothing
    // else would ever ask again.
    const reportWhenClear = () => {
      if (reportOnce() || recheck !== undefined) return;
      recheck = setInterval(reportOnce, 1_000);
    };

    if (typeof IntersectionObserver === "undefined") {
      // No observer (older webview, test renderer): fall back to the page
      // visibility rule rather than never acknowledging anything.
      reportWhenClear();
      return stopRechecking;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) reportWhenClear();
        else stopRechecking(); // panned away: stop until it comes back
      },
      { threshold: 0.01 },
    );
    observer.observe(element);
    return () => {
      observer.disconnect();
      stopRechecking();
    };
  }, [shown, report]);

  return ref;
}
