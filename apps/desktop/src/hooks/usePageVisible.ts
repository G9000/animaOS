import { useEffect, useState } from "react";

/**
 * Whether the page is currently visible to the user.
 *
 * Used by the Dashboard to keep an ambient dream off screen while the window
 * is hidden (IL-015 / PR #135 review, P1). A greeting rendered into a
 * background window can outlive its server-side claim; revealing that stale
 * frame would disclose a dream the server has already re-offered elsewhere,
 * so the dream is withheld until the claim has been re-confirmed.
 */
export function usePageVisible(): boolean {
  const [visible, setVisible] = useState(() =>
    typeof document === "undefined" ? true : document.visibilityState === "visible",
  );

  useEffect(() => {
    if (typeof document === "undefined") return;
    const sync = () => setVisible(document.visibilityState === "visible");
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);

  return visible;
}
