import { useEffect, useRef, useState } from "react";
import { api } from "../../../lib/api";

// Relocated (Task 12) from panels/PageHeader.tsx, which no longer needs it
// once cover/attachment rendering moved to DetailsDrawer — this is now the
// single shared place a diary attachment's blob is fetched and turned into
// an object URL, reused by LibrarySidebar's entry-list cover thumbnail and
// DetailsDrawer's cover banner / attachment previews.
//
// `retryToken` is new in Task 12 (default 0, harmless for pre-existing
// callers): bumping it re-runs the fetch without needing the attachment's
// identity to change, which DetailsDrawer's attachment-preview-failure
// retry action (brief Step 6) depends on.
export function useAttachmentBlobUrl(
  attachment: { entryId: number; id: number } | null | undefined,
  onError?: (message: string) => void,
  retryToken = 0,
): string | null {
  const [url, setUrl] = useState<string | null>(null);

  // Fix round 1, Finding 1 (CRITICAL): `onError` used to sit directly in
  // the fetch effect's dependency array. Every caller in this codebase
  // (LibrarySidebar, DetailsDrawer, and — Task 13 — AttachmentImageView)
  // passes an inline arrow, which is a fresh function identity on every
  // render of the CALLING component. That component re-renders as a direct
  // consequence of this hook's own `setUrl` call below (the state update
  // belongs to the calling component's fiber), so the sequence was:
  // fetch resolves -> setUrl -> caller re-renders -> new `onError` identity
  // -> effect deps changed -> cleanup revokes the just-created object URL
  // -> effect re-runs -> fetch again -> forever. Reproduced at 16k+
  // fetches in 300ms against an instantly-resolving stub, with the live
  // `<img src>` being revoked out from under the element every cycle.
  //
  // Fix: hold the latest `onError` in a ref, updated by its own effect,
  // and read it from inside the fetch effect instead of depending on it
  // directly. The fetch effect's deps now describe only what should
  // actually trigger a re-fetch: which attachment, and the caller-driven
  // `retryToken` bump. This fixes BOTH existing call sites at once,
  // without requiring either caller to memoize its callback.
  const onErrorRef = useRef(onError);
  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  useEffect(() => {
    if (!attachment) {
      setUrl(null);
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    api.diary
      .downloadAttachment(attachment.entryId, attachment.id)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch((err) => {
        if (!cancelled) {
          onErrorRef.current?.(err instanceof Error ? err.message : "Failed to load attachment.");
        }
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attachment?.entryId, attachment?.id, retryToken]);

  return url;
}
