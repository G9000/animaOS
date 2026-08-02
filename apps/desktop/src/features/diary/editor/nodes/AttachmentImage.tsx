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
}

interface DiaryImageStorage {
  // Files staged for upload, keyed by the transient `localId` attribute
  // generated at insertion time. Not schema state — never touches
  // getHTML() — just a place for the NodeView's upload effect (and a
  // retry) to find the File object that belongs to a given node instance.
  // Cleared on successful upload; left in place on failure so Retry can
  // re-attempt without asking the user to re-select the file.
  pendingFiles: Map<string, File>;
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

  // Drives BOTH the initial upload (fired once, right after insertion, when
  // uploadState starts as "uploading") and every retry (Retry just flips
  // uploadState back to "uploading" via updateAttributes, which re-runs
  // this effect). `updateAttributes` is bound to THIS node instance via
  // Tiptap's own getPos() tracking, so there is no manual "find the node
  // by position" step here — that sidesteps the position-drift hazard a
  // manual position lookup would have if the document changed elsewhere
  // while the upload was in flight.
  useEffect(() => {
    if (uploadState !== "uploading") return;
    const file = localId ? storage.pendingFiles.get(localId) : undefined;
    if (!file) {
      // No cached file to (re)upload — e.g. storage was cleared by a full
      // page reload mid-upload. Surface as an error rather than spinning
      // forever; there is nothing left to retry against.
      updateAttributes({ uploadState: "error" });
      return;
    }
    let cancelled = false;
    options
      .uploadImage(file)
      .then((uploadedId) => {
        if (cancelled) return;
        if (uploadedId === null) {
          updateAttributes({ uploadState: "error" });
          return;
        }
        if (localId) storage.pendingFiles.delete(localId);
        updateAttributes({ attachmentId: uploadedId, uploadState: "ready" });
      })
      .catch(() => {
        if (!cancelled) updateAttributes({ uploadState: "error" });
      });
    return () => {
      cancelled = true;
    };
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
        <div className="flex items-center justify-center gap-2 rounded-xl border border-border/60 bg-secondary/40 px-3 py-8 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground animate-pulse">
          Uploading{caption ? ` "${caption}"` : " image"}…
        </div>
      )}

      {uploadState === "error" && (
        <button
          type="button"
          onClick={handleUploadRetry}
          className="flex w-full items-center justify-center gap-2 rounded-xl border border-destructive/40 bg-destructive/10 px-3 py-4 font-mono text-[10px] uppercase tracking-[0.12em] text-destructive hover:bg-destructive/20"
        >
          Failed to upload{caption ? ` "${caption}"` : ""} — click to retry
        </button>
      )}

      {uploadState === "ready" &&
        (downloadFailed ? (
          <button
            type="button"
            onClick={handleDownloadRetry}
            className="flex w-full items-center justify-center gap-2 rounded-xl border border-destructive/40 bg-destructive/10 px-3 py-4 font-mono text-[10px] uppercase tracking-[0.12em] text-destructive hover:bg-destructive/20"
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
          <div className="flex items-center justify-center rounded-xl border border-border/60 bg-secondary/40 px-3 py-8 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground animate-pulse">
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
// rather than an embedded data URL. `parseHTML` below matches
// `img[data-attachment-id]`, which ProseMirror's schema treats as more
// specific than the stock node's `img[src]` rule, so a saved body
// containing both kinds of <img> parses each back into the correct node
// type.
export const DiaryImage = Node.create<DiaryImageOptions, DiaryImageStorage>({
  name: "diaryImage",
  group: "block",
  draggable: true,
  selectable: true,

  addOptions() {
    return {
      entryId: null,
      uploadImage: async () => null,
    };
  },

  addStorage() {
    return { pendingFiles: new Map() };
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
    return [{ tag: "img[data-attachment-id]" }];
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
