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
 *
 * Occlusion by an overlay (the gallery lightbox, a modal) is not observable
 * here; the Dashboard withholds `report` while one is open.
 *
 * Nothing is queued or deferred. If the greeting is not really visible no
 * receipt is owed, the claim simply lapses, and the dream is offered again —
 * the correct failure for this feature.
 */
export function useDreamShownReceipt(
  shown: boolean,
  report?: () => void,
): React.RefObject<HTMLDivElement | null> {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!shown || !report) return;
    // No document (static render): nothing was painted, so nothing is
    // reported — never guess in the direction of consuming a dream.
    if (typeof document === "undefined") return;
    const element = ref.current;
    if (!element) return;

    let reported = false;
    const reportOnce = () => {
      if (reported || document.visibilityState !== "visible") return;
      reported = true;
      report();
    };

    if (typeof IntersectionObserver === "undefined") {
      // No observer (older webview, test renderer): fall back to the page
      // visibility rule rather than never acknowledging anything.
      reportOnce();
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) reportOnce();
      },
      { threshold: 0.01 },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [shown, report]);

  return ref;
}
