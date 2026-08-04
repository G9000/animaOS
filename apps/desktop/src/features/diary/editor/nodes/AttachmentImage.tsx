import { useEffect, useState } from "react";
import { Node, mergeAttributes, type NodeViewProps } from "@tiptap/core";
import { NodeViewWrapper, ReactNodeViewRenderer } from "@tiptap/react";
import { cn } from "@anima/standard-templates";
// `hooks/useAttachmentBlobUrl` is the one existing place a diary
// attachment's blob is fetched and turned into a revocable object URL
// (LibrarySidebar's cover thumbnail, DetailsDrawer's cover/attachment
// previews). Reused here rather than duplicating that fetch+revoke
// lifecycle a second time. This is the one file under editor/ that reaches
// something which itself calls the API client — see the note on
// DiaryImageOptions below for why that is the accepted shape of the
// editor/ boundary rule, not a violation of it: the actual network calls
// (upload AND download) are owned by hooks outside editor/ (useDiaryEntries,
// useAttachmentBlobUrl); nothing in this file constructs a request itself
// or imports `lib/api`.
import { useAttachmentBlobUrl } from "../../hooks/useAttachmentBlobUrl";

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    diaryImage: {
      /**
       * Inserts a diaryImage node at the current selection in the
       * "uploading" state and kicks off the upload via the extension's
       * configured `uploadImage` option. The caller (DiaryEditor's
       * handlePaste/handleDrop, or DiaryWorkspace's slash-menu image
       * picker) never has to track the node's position itself — see the
       * doc comment on AttachmentImageView's upload effect for why.
       */
      insertAttachmentImage: (file: File) => ReturnType;
    };
  }
}

export interface DiaryImageOptions {
  // Which entry this editor instance belongs to. Needed by the NodeView to
  // resolve `{ entryId, id }` for useAttachmentBlobUrl — the node's own
  // attrs only carry the attachment id, not the entry id (an attachment
  // always belongs to whichever entry's document contains it).
  entryId: number | null;
  // The only place a network call is threaded into editor/: supplied by
  // DiaryWorkspace (via DiaryEditor's `onImageUpload` prop ->
  // createDiaryExtensions), exactly the way `onImageRequest` already
  // crosses this boundary. This node never imports the API client itself;
  // it only ever calls this callback.
  uploadImage: (file: File) => Promise<number | null>;
  // Round 5 fix (P1: "silent partial success" — a successful upload
  // discarded because the user navigated away before it resolved). Called
  // when an upload finishes successfully AFTER this node's NodeView has
  // already unmounted (entry switch, leaving /journal, etc.) — see
  // `handleUploadResolution` below for the full mechanism. `uploadImage`
  // has already produced a real attachment row against THIS options
  // object's `entryId` (that's what the server call is addressed to,
  // regardless of NodeView lifetime), so there is nothing unsafe to do
  // here — this is purely a "go tell someone" callback, never a place
  // that touches an editor.
  onUploadOrphaned?: (entryId: number, attachmentId: number) => void;
}

// Round 5 fix: previously each NodeView instance owned its own `let
// cancelled` flag, set on unmount, and skipped `updateAttributes` on a
// completed upload if the flag was set. That discarded SUCCESSFUL uploads
// silently — the server already has the attachment, but nothing ever
// wrote its id anywhere the user could find it. `liveHandlers` replaces
// that flag: instead of "was I torn down", the completion logic asks "is
// there still a mounted NodeView for this localId that I can safely hand
// the result to" — a fact tracked independently of the upload's own
// effect lifetime, so it survives the NodeView unmounting mid-upload.
interface DiaryImageStorage {
  // Files staged for upload, keyed by the transient `localId` attribute
  // generated at insertion time. Not schema state — never touches
  // getHTML() — just a place for the NodeView's upload effect (and a
  // retry) to find the File object that belongs to a given node instance.
  // Cleared on successful upload; left in place on failure so Retry can
  // re-attempt without asking the user to re-select the file.
  pendingFiles: Map<string, File>;
  // Registered by a mounted NodeView (see the dedicated mount-tracking
  // effect below) for as long as it is alive, keyed by the same `localId`.
  // Looked up by `handleUploadResolution` at the moment an upload
  // resolves — NOT by anything captured when the upload started — so it
  // always reflects whether THIS node is mounted right now, not whether
  // it was mounted when the request went out.
  liveHandlers: Map<string, (outcome: UploadOutcome) => void>;
  // Round 6 fix (P2): an in-flight guard keyed by `localId`, independent of
  // any single effect invocation's lifetime. Round 5 removed the upload
  // effect's `cancelled` flag (correctly — it discarded successful
  // uploads), but left no guard at all against the effect body itself
  // running twice for the same node (React Strict Mode's mount -> cleanup
  // -> mount is the dev-time trigger, but any effect re-run for any reason
  // is equally exposed). Marks "an upload has been started for this
  // localId and hasn't resolved yet" so a second invocation can skip
  // calling `uploadImage` again — see `claimUploadSlot` below. Cleared by
  // `handleUploadResolution` on genuine completion (success OR failure) so
  // a legitimate Retry can still re-upload.
  uploadsInFlight: Set<string>;
}

export type UploadOutcome =
  | { status: "ready"; attachmentId: number }
  | { status: "error" };

// Pure, unit-testable: "is the originating editor still available to
// receive this upload's result" reduces to exactly this lookup. No React,
// no network — safe to test with a bare Map.
export function isEditorAvailable(storage: DiaryImageStorage, localId: string): boolean {
  return storage.liveHandlers.has(localId);
}

// Round 6 fix (P2): the in-flight guard's decision, factored out pure and
// unit-testable exactly like `isEditorAvailable` above — no React, no
// network, safe to test with a bare Set. Returns true (and claims the
// slot) the FIRST time it is asked about a given `localId`; every
// subsequent call before that upload resolves returns false without
// mutating anything. The caller (the upload effect below) uses this to
// decide whether to actually call `uploadImage` — NOT whether to register
// as an observer, which is handled unconditionally by the separate
// mount-tracking effect regardless of this guard's answer.
export function claimUploadSlot(storage: DiaryImageStorage, localId: string): boolean {
  if (storage.uploadsInFlight.has(localId)) return false;
  storage.uploadsInFlight.add(localId);
  return true;
}

// The other half of the completion contract, also pure and unit-testable:
// given the upload's raw result, decide what happens next. Mounted ->
// forward to the live handler (which calls updateAttributes on the correct
// node). Not mounted and successful -> this is the "silent partial
// success" case; clean up pendingFiles so it can't leak and report the
// orphan via `onOrphaned` so the caller can surface a notice and refresh
// the attachments list. Not mounted and failed -> deliberately left alone,
// same as the pre-existing accepted limitation (Task 13): a discarded
// FAILURE with no server-side trace is honest, unlike a discarded success.
export function handleUploadResolution(
  storage: DiaryImageStorage,
  localId: string,
  uploadedId: number | null,
  onOrphaned: (attachmentId: number) => void,
): void {
  // Round 6 fix: cleared on every genuine completion, success or failure
  // alike, so a legitimate Retry (which flips uploadState back to
  // "uploading" and re-runs the upload effect) is free to claim the slot
  // again rather than being permanently blocked by this round's guard.
  storage.uploadsInFlight.delete(localId);
  const handler = storage.liveHandlers.get(localId);
  if (uploadedId === null) {
    handler?.({ status: "error" });
    return;
  }
  storage.pendingFiles.delete(localId);
  if (handler) {
    handler({ status: "ready", attachmentId: uploadedId });
  } else {
    onOrphaned(uploadedId);
  }
}

let localIdCounter = 0;
function nextLocalId(): string {
  localIdCounter += 1;
  return `pending-${Date.now()}-${localIdCounter}`;
}

type UploadState = "uploading" | "ready" | "error";

function AttachmentImageView(props: NodeViewProps) {
  const { node, updateAttributes, extension, selected } = props;
  const attachmentId = node.attrs.attachmentId as number | null;
  const caption = node.attrs.caption as string | null;
  const uploadState = node.attrs.uploadState as UploadState;
  const localId = node.attrs.localId as string | null;
  const options = extension.options as DiaryImageOptions;
  const storage = extension.storage as DiaryImageStorage;

  // Round 5 fix: registers this instance's result handler in
  // `storage.liveHandlers` for as long as it is mounted, and nothing else.
  // Deliberately a SEPARATE effect from the upload-kicking one below —
  // this one's only job is answering "is a NodeView for this localId alive
  // right now" at whatever moment an upload happens to resolve, which may
  // be long after (or long before) the upload effect below has re-run.
  // `updateAttributes` is bound to THIS node instance via Tiptap's own
  // getPos() tracking, so calling it from here is exactly as safe as
  // calling it directly inside the upload effect used to be — this only
  // changes WHEN it's safe to call, not how.
  useEffect(() => {
    if (!localId) return undefined;
    storage.liveHandlers.set(localId, (outcome) => {
      if (outcome.status === "ready") {
        updateAttributes({ attachmentId: outcome.attachmentId, uploadState: "ready" });
      } else {
        updateAttributes({ uploadState: "error" });
      }
    });
    return () => {
      storage.liveHandlers.delete(localId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [localId]);

  // Drives BOTH the initial upload (fired once, right after insertion, when
  // uploadState starts as "uploading") and every retry (Retry just flips
  // uploadState back to "uploading" via updateAttributes, which re-runs
  // this effect). There is no manual "find the node by position" step
  // here — that sidesteps the position-drift hazard a manual position
  // lookup would have if the document changed elsewhere while the upload
  // was in flight.
  //
  // Round 5 fix: this effect's cleanup no longer flips a `cancelled` flag
  // that the completion handlers below check — that was the bug (P1: a
  // successful upload silently discarded because the user navigated away
  // before it resolved, while the server had already created the
  // attachment — see `handleUploadResolution`'s doc comment). The upload's
  // completion is now unconditional; what happens with the RESULT is
  // decided by `handleUploadResolution` at resolution time, based on
  // whether a NodeView is still registered in `storage.liveHandlers` —
  // not by whether THIS effect instance is still alive.
  useEffect(() => {
    if (uploadState !== "uploading") return;
    const file = localId ? storage.pendingFiles.get(localId) : undefined;
    if (!file || !localId) {
      // No cached file to (re)upload — e.g. storage was cleared by a full
      // page reload mid-upload. Surface as an error rather than spinning
      // forever; there is nothing left to retry against.
      updateAttributes({ uploadState: "error" });
      return;
    }
    // Round 6 fix (P2): guards against a second `uploadImage` call for this
    // same `localId` when this effect's body runs more than once before the
    // first call resolves (React Strict Mode's mount -> cleanup -> mount is
    // the dev-time reproduction, but the gap was real regardless of cause —
    // round 5 removed the old `cancelled` flag and left nothing in its
    // place). The mount-tracking effect above still registers this
    // instance's handler in `storage.liveHandlers` unconditionally, so
    // whichever NodeView ends up mounted when the ONE in-flight upload
    // resolves still receives the result via `handleUploadResolution` —
    // this only suppresses the redundant network call, never the outcome.
    if (!claimUploadSlot(storage, localId)) return;
    options
      .uploadImage(file)
      .then((uploadedId) => {
        handleUploadResolution(storage, localId, uploadedId, (attachmentId) => {
          if (options.entryId !== null) {
            options.onUploadOrphaned?.(options.entryId, attachmentId);
          }
        });
      })
      .catch(() => {
        handleUploadResolution(storage, localId, null, () => {});
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploadState, localId]);

  const [downloadRetryToken, setDownloadRetryToken] = useState(0);
  const [downloadFailed, setDownloadFailed] = useState(false);

  const attachment =
    uploadState === "ready" && attachmentId !== null && options.entryId !== null
      ? { entryId: options.entryId, id: attachmentId }
      : null;

  const blobUrl = useAttachmentBlobUrl(attachment, () => setDownloadFailed(true), downloadRetryToken);

  const handleUploadRetry = () => updateAttributes({ uploadState: "uploading" });
  const handleDownloadRetry = () => {
    setDownloadFailed(false);
    setDownloadRetryToken((token) => token + 1);
  };

  return (
    <NodeViewWrapper
      data-type="diary-image"
      className={cn(
        "my-2 rounded-xl",
        selected && "ring-2 ring-accent ring-offset-2 ring-offset-background",
      )}
    >
      {uploadState === "uploading" && (
        <div className="flex items-center justify-center gap-2 rounded-xl border border-border/60 bg-secondary/40 px-3 py-8 font-mono text-caption uppercase tracking-caps-2 text-muted-foreground animate-pulse">
          Uploading{caption ? ` "${caption}"` : " image"}…
        </div>
      )}

      {uploadState === "error" && (
        <button
          type="button"
          onClick={handleUploadRetry}
          className="flex w-full items-center justify-center gap-2 rounded-xl border border-destructive/40 bg-destructive/10 px-3 py-4 font-mono text-caption uppercase tracking-caps-2 text-destructive hover:bg-destructive/20"
        >
          Failed to upload{caption ? ` "${caption}"` : ""} — click to retry
        </button>
      )}

      {uploadState === "ready" &&
        (downloadFailed ? (
          <button
            type="button"
            onClick={handleDownloadRetry}
            className="flex w-full items-center justify-center gap-2 rounded-xl border border-destructive/40 bg-destructive/10 px-3 py-4 font-mono text-caption uppercase tracking-caps-2 text-destructive hover:bg-destructive/20"
          >
            Failed to load image — click to retry
          </button>
        ) : blobUrl ? (
          <img
            src={blobUrl}
            alt={caption ?? ""}
            className="mx-auto block max-h-[32rem] rounded-xl border border-border/60 shadow-lg"
          />
        ) : (
          <div className="flex items-center justify-center rounded-xl border border-border/60 bg-secondary/40 px-3 py-8 font-mono text-caption uppercase tracking-caps-2 text-muted-foreground animate-pulse">
            Loading image…
          </div>
        ))}
    </NodeViewWrapper>
  );
}

// A `diaryImage` node, distinct from (and coexisting alongside) the stock
// `image` node registered in extensions.ts. The stock node keeps
// `allowBase64: true` for legacy entries whose bodies already contain
// `<img src="data:image/png;base64,…">`; every NEW inline image goes
// through this node instead, backed by the encrypted attachment store
// rather than an embedded data URL.
//
// Fix round 1, Finding 3: ProseMirror's DOMParser does NOT pick a parse
// rule by "specificity" the way CSS selectors do — it tries rules in order
// of `priority` (default 50 for every rule unless set otherwise), and
// within equal priority, in extension REGISTRATION order. `TiptapImage` is
// registered before `DiaryImage` in extensions.ts, so without an explicit
// priority bump here, an `<img>` carrying BOTH `src` and
// `data-attachment-id` (verified: `<img src="data:…" data-attachment-id="5">`)
// would match the stock node's `img[src]` rule first and silently drop the
// attachment id. Coexistence with legacy base64 images does not actually
// depend on this priority in practice — `diaryImage`'s own `renderHTML`
// never emits a `src` attribute, so the two rules' match sets never
// overlap for content this node itself produced — but that was a fragile
// invariant resting on the wrong stated mechanism, so `priority: 100` is
// set explicitly to make correct precedence part of the contract rather
// than an accident of never emitting `src`.
export const DiaryImage = Node.create<DiaryImageOptions, DiaryImageStorage>({
  name: "diaryImage",
  group: "block",
  draggable: true,
  selectable: true,

  addOptions() {
    return {
      entryId: null,
      uploadImage: async () => null,
      onUploadOrphaned: undefined,
    };
  },

  addStorage() {
    return { pendingFiles: new Map(), liveHandlers: new Map(), uploadsInFlight: new Set() };
  },

  addAttributes() {
    return {
      attachmentId: {
        default: null,
        parseHTML: (element) => {
          const raw = element.getAttribute("data-attachment-id");
          return raw === null ? null : Number(raw);
        },
        renderHTML: (attributes) =>
          attributes.attachmentId === null
            ? {}
            : { "data-attachment-id": String(attributes.attachmentId) },
      },
      caption: {
        default: null,
        parseHTML: (element) => element.getAttribute("alt"),
        renderHTML: (attributes) =>
          attributes.caption ? { alt: attributes.caption as string } : {},
      },
      // Transient UI state. rendered:false keeps it out of getHTML() entirely,
      // so an in-flight upload can never be persisted into an entry body.
      uploadState: { default: "ready", rendered: false },
      // Transient bookkeeping only (see DiaryImageStorage above) — never
      // rendered, never round-tripped. Lets the NodeView's upload effect
      // find this node's staged File without a document-wide position
      // search.
      localId: { default: null, rendered: false },
    };
  },

  parseHTML() {
    return [{ tag: "img[data-attachment-id]", priority: 100 }];
  },

  renderHTML({ HTMLAttributes }) {
    return ["img", mergeAttributes(HTMLAttributes)];
  },

  addNodeView() {
    return ReactNodeViewRenderer(AttachmentImageView);
  },

  addCommands() {
    return {
      insertAttachmentImage:
        (file: File) =>
        ({ commands }) => {
          const localId = nextLocalId();
          this.storage.pendingFiles.set(localId, file);
          return commands.insertContent({
            type: this.name,
            attrs: {
              attachmentId: null,
              caption: file.name,
              uploadState: "uploading",
              localId,
            },
          });
        },
    };
  },
});
