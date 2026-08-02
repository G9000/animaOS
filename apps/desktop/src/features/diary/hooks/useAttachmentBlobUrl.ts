import { useEffect, useState } from "react";
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
          onError?.(err instanceof Error ? err.message : "Failed to load attachment.");
        }
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attachment?.entryId, attachment?.id, onError, retryToken]);

  return url;
}
