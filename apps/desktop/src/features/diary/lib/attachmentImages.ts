// Task 13 fix round 1, Finding 2: a diaryImage node with no attachmentId
// yet (still uploading, or its upload failed) renders to `<img alt="…">`
// — no `src`, no `data-attachment-id` (see renderHTML/addAttributes in
// editor/nodes/AttachmentImage.tsx). If that string reaches the SAVED
// body — an autosave fires while an upload is in flight, or after it
// fails and the user switches entries before retrying — it silently
// disappears on the next load: it matches neither the stock image node's
// `img[src]` parse rule nor diaryImage's `img[data-attachment-id]` rule,
// so ProseMirror's DOMParser drops the element with no trace and no
// error. Rather than let that happen implicitly on reparse, this strips
// such placeholders out of the HTML BEFORE it is ever persisted, so the
// persisted body never contains an addressable-less image tag in the
// first place — the disappearance is deliberate, immediate, and total,
// not a delayed accidental schema-parse-miss.
//
// Consequence for the retry affordance: a pending or failed image's retry
// chip, and the cached File backing it, live only in the current editor
// instance's memory (DiaryImageStorage in editor/nodes/AttachmentImage.tsx
// — a plain in-memory Map, never persisted). Switching entries remounts
// DiaryEditor (keyed by entry.id) from the saved body, which — because of
// this function — never contains that placeholder to begin with. So
// retry does NOT survive an entry switch: that specific paste/drop/slash-
// image attempt is simply gone, exactly as if it had never been started.
// This is an accepted, explicit trade-off, not an oversight: no
// half-written or orphaned reference survives into the persisted body
// either way, and losing the retry option is disclosed here rather than
// being a silent surprise discovered by reparsing.
export function stripUnresolvedAttachmentImages(html: string, doc: Document): string {
  if (!html.includes("<img")) return html;
  const container = doc.createElement("div");
  container.innerHTML = html;
  for (const img of Array.from(container.querySelectorAll("img"))) {
    if (!img.hasAttribute("data-attachment-id") && !img.hasAttribute("src")) {
      img.remove();
    }
  }
  return container.innerHTML;
}
