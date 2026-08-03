import { useEffect, useRef, useState } from "react";
import { cn, FileIcon, ImageIcon, MicIcon, XIcon } from "@anima/standard-templates";
import type { DiaryAttachmentData, DiaryEntryData, DiaryEntryUpdateData, DiaryFolderData } from "@anima/api-client";
import { formatFileSize, formatTimestamp, countWords, isPreviewableAttachment } from "../lib/textFormat";
import { Glyph } from "../editor/glyphIcons";
import { useAttachmentBlobUrl } from "../hooks/useAttachmentBlobUrl";

function StarGlyphIcon({ className }: { className?: string } = {}) {
  return (
    <Glyph className={className}>
      <path
        d="M12 3.5l2.4 5 5.4.6-4 3.7 1.1 5.4L12 15.5l-4.9 2.7 1.1-5.4-4-3.7 5.4-.6L12 3.5Z"
        fill="currentColor"
        stroke="none"
      />
    </Glyph>
  );
}

function CalendarGlyphIcon({ className }: { className?: string } = {}) {
  return (
    <Glyph className={className}>
      <rect x="4" y="5" width="16" height="15" rx="0" />
      <path d="M4 9.5h16M8 3v4M16 3v4" />
    </Glyph>
  );
}

function FolderGlyphIcon({ className }: { className?: string } = {}) {
  return (
    <Glyph className={className}>
      <path d="M4 6h5l2 2h9v11H4V6Z" />
    </Glyph>
  );
}

function attachmentIcon(kind: string) {
  if (kind === "image") return ImageIcon;
  if (kind === "audio") return MicIcon;
  return FileIcon;
}

function CoverBanner({
  attachment,
  onError,
}: {
  attachment: DiaryAttachmentData;
  onError: (message: string) => void;
}) {
  const url = useAttachmentBlobUrl(attachment, onError);

  if (!url) {
    return <div className="w-full h-40 rounded-xl bg-secondary/40 animate-pulse" />;
  }
  return <img src={url} alt="" className="w-full h-40 rounded-xl object-cover" />;
}

// Attachment preview failure (brief Step 6): rather than an infinite
// pulsing skeleton when the blob fetch fails, keep the filename visible
// and offer Retry (re-run the fetch via `retryToken`) or Download (the
// existing open/download flow).
function AttachmentPreview({
  attachment,
  onError,
  onOpenAttachment,
}: {
  attachment: DiaryAttachmentData;
  onError: (message: string) => void;
  onOpenAttachment: (attachment: DiaryAttachmentData) => void;
}) {
  const [failed, setFailed] = useState(false);
  const [retryToken, setRetryToken] = useState(0);
  const previewUrl = useAttachmentBlobUrl(
    attachment,
    (message) => {
      setFailed(true);
      onError(message);
    },
    retryToken,
  );

  if (failed) {
    return (
      <div className="w-40 rounded-lg border border-destructive/30 bg-destructive/5 px-2.5 py-2 space-y-1.5">
        <p className="truncate text-caption text-muted-foreground">
          {attachment.filename || attachment.kind}
        </p>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setFailed(false);
              setRetryToken((t) => t + 1);
            }}
            className="font-mono text-[9px] uppercase tracking-[0.14em] text-accent hover:text-accent/80"
          >
            Retry
          </button>
          <button
            type="button"
            onClick={() => onOpenAttachment(attachment)}
            className="font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground"
          >
            Download
          </button>
        </div>
      </div>
    );
  }

  if (!previewUrl) {
    return <div className="w-40 h-24 rounded-lg bg-secondary/40 animate-pulse" />;
  }

  if (attachment.kind === "image") {
    return (
      <img
        src={previewUrl}
        alt={attachment.filename || "Diary attachment"}
        className="max-h-64 max-w-full rounded-lg border border-foreground/[0.08] object-contain"
      />
    );
  }

  if (attachment.kind === "audio") {
    return <audio controls src={previewUrl} className="h-9 max-w-full" />;
  }

  return (
    <video controls src={previewUrl} className="max-h-64 max-w-full rounded-lg border border-foreground/[0.08]" />
  );
}

export interface DetailsDrawerProps {
  entry: DiaryEntryData;
  folders: DiaryFolderData[];
  open: boolean;
  onClose: () => void;
  onUpdate: (data: DiaryEntryUpdateData) => void;
  onDelete: () => void;
  // Deviations beyond the brief's literal prop list: cover/attachment
  // upload need a real File (not expressible in DiaryEntryUpdateData), and
  // download/error reporting are presentation concerns that live here now
  // that the attachment list itself moved into this panel. See
  // task-12-report.md.
  onCoverFileSelected: (file: File) => void;
  onFilesSelected: (files: FileList | null) => void;
  onOpenAttachment: (attachment: DiaryAttachmentData) => void;
  onAttachmentError: (message: string) => void;
  // Live (unsaved-to-server) editor text, for word/char counts — the
  // voice-note recorder also moved here from the composer (Step 3), so its
  // controls are rendered from these same props.
  bodyText: string;
  recording: boolean;
  speechAvailable: boolean;
  liveTranscript: string;
  onToggleRecording: () => void;
}

export function DetailsDrawer({
  entry,
  folders,
  open,
  onClose,
  onUpdate,
  onDelete,
  onCoverFileSelected,
  onFilesSelected,
  onOpenAttachment,
  onAttachmentError,
  bodyText,
  recording,
  speechAvailable,
  liveTranscript,
  onToggleRecording,
}: DetailsDrawerProps) {
  // Finding 3 (PR #139): this initializer only ever runs on first mount.
  // The caller now renders this component `key={entry.id}` (see
  // DiaryWorkspace.tsx), so "first mount" happens again for every entry —
  // this draft can no longer be initialized from one entry and silently
  // carried over (and, on blur, committed onto) a different one after the
  // selection changes. Without that key, this state would keep whatever
  // the previous entry's mood was across a switch. A scan of this
  // component's other useState calls (AttachmentPreview's `failed` /
  // `retryToken`) turned up nothing else keyed off the initial `entry`
  // prop this way — those are already scoped per-attachment by their own
  // `key={attachment.id}`, and a full DetailsDrawer remount clears them
  // regardless.
  const [moodValue, setMoodValue] = useState(entry.mood ?? "");
  const moodValueRef = useRef(moodValue);
  moodValueRef.current = moodValue;
  // Tracks the mood value this drawer has last dispatched (or that the
  // entry already had) so a debounced/flushed commit never re-sends an
  // identical value twice in a row.
  const lastCommittedMoodRef = useRef(entry.mood ?? "");
  const onUpdateRef = useRef(onUpdate);
  onUpdateRef.current = onUpdate;
  const moodTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearMoodTimer = () => {
    if (moodTimerRef.current !== null) {
      clearTimeout(moodTimerRef.current);
      moodTimerRef.current = null;
    }
  };

  const commitMoodValue = (value: string) => {
    clearMoodTimer();
    const trimmed = value.trim();
    if (trimmed === lastCommittedMoodRef.current) return;
    lastCommittedMoodRef.current = trimmed;
    onUpdateRef.current({ mood: trimmed || undefined, clearMood: !trimmed });
  };

  // Also related to Finding 3: mood used to commit ONLY on blur, so a
  // typed value was dropped if the field never blurred before the drawer
  // went away (switching entries — now an unmount, given the key above —
  // or the whole workspace unmounting). A short debounce means typing
  // alone eventually commits without waiting for blur, and this cleanup
  // flushes whatever was last typed the moment this instance is torn
  // down, so neither path can silently lose it.
  useEffect(() => {
    return () => {
      commitMoodValue(moodValueRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const scheduleMoodCommit = (value: string) => {
    clearMoodTimer();
    moodTimerRef.current = setTimeout(() => commitMoodValue(value), 600);
  };

  if (!open) return null;

  const coverAttachment =
    entry.coverAttachmentId != null
      ? (entry.attachments.find((a) => a.id === entry.coverAttachmentId) ?? null)
      : null;

  const previewableAttachments = entry.attachments.filter(
    (a) => isPreviewableAttachment(a.kind) && a.id !== entry.coverAttachmentId,
  );
  const otherAttachments = entry.attachments.filter((a) => !isPreviewableAttachment(a.kind));

  const wordCount = countWords(bodyText);
  const charCount = bodyText.length;

  return (
    <aside className="w-80 shrink-0 rounded-xl border border-foreground/[0.08] bg-background/95 backdrop-blur-[36px] shadow-[0_4px_28px_rgba(0,0,0,0.18)] flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-5 py-4 border-b border-foreground/[0.08]">
        <h2 className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground/70">Details</h2>
        <button
          type="button"
          onClick={onClose}
          title="Close details"
          className="text-muted-foreground/50 hover:text-foreground"
        >
          <XIcon size="sm" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
        <section className="space-y-2">
          {coverAttachment ? (
            <div className="relative group">
              <CoverBanner attachment={coverAttachment} onError={onAttachmentError} />
              <button
                type="button"
                onClick={() => onUpdate({ clearCover: true })}
                className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity inline-flex items-center gap-1.5 rounded-lg bg-background/80 border border-foreground/[0.1] px-2 py-1 font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground hover:text-destructive"
              >
                <XIcon size="sm" />
                Remove cover
              </button>
            </div>
          ) : (
            <label className="w-full h-12 rounded-xl border border-dashed border-foreground/[0.12] flex items-center justify-center gap-2 text-muted-foreground/60 hover:text-foreground hover:border-foreground/25 transition-colors cursor-pointer">
              <ImageIcon size="sm" />
              <span className="font-mono text-[10px] uppercase tracking-[0.14em]">Add cover image</span>
              <input
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) onCoverFileSelected(file);
                  event.target.value = "";
                }}
              />
            </label>
          )}
        </section>

        <section className="space-y-2">
          <label className="flex items-center gap-2 font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground/60">
            <CalendarGlyphIcon className="size-3.5" />
            Date
          </label>
          <input
            type="date"
            value={entry.entryDate}
            onChange={(event) => {
              if (event.target.value === entry.entryDate) return;
              onUpdate({ entryDate: event.target.value });
            }}
            className="w-full bg-foreground/[0.04] border border-foreground/[0.08] rounded-lg px-2.5 py-1.5 text-detail text-foreground outline-none focus:border-accent/50"
          />
        </section>

        <section className="space-y-2">
          <label className="font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground/60">Mood</label>
          <input
            type="text"
            value={moodValue}
            onChange={(event) => {
              const { value } = event.target;
              setMoodValue(value);
              scheduleMoodCommit(value);
            }}
            onBlur={(event) => commitMoodValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") (event.target as HTMLInputElement).blur();
            }}
            placeholder="How are you feeling?"
            maxLength={80}
            className="w-full bg-foreground/[0.04] border border-foreground/[0.08] rounded-lg px-2.5 py-1.5 text-detail text-foreground placeholder:text-muted-foreground/40 outline-none focus:border-accent/50"
          />
        </section>

        {folders.length > 0 && (
          <section className="space-y-2">
            <label className="flex items-center gap-2 font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground/60">
              <FolderGlyphIcon className="size-3.5" />
              Folder
            </label>
            <select
              value={entry.folderId ?? ""}
              onChange={(event) =>
                onUpdate({
                  folderId: event.target.value ? Number(event.target.value) : undefined,
                  clearFolder: !event.target.value,
                })
              }
              className="w-full bg-foreground/[0.04] border border-foreground/[0.08] rounded-lg px-2.5 py-1.5 text-detail text-foreground outline-none focus:border-accent/50"
            >
              <option value="">No folder</option>
              {folders.map((folder) => (
                <option key={folder.id} value={folder.id}>
                  {folder.name}
                </option>
              ))}
            </select>
          </section>
        )}

        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground/60">Attachments</p>
            <label className="inline-flex items-center gap-1 cursor-pointer font-mono text-[9px] uppercase tracking-[0.14em] text-accent hover:text-accent/80">
              <FileIcon size="sm" />
              Attach
              <input
                type="file"
                multiple
                accept="image/*,audio/*,video/*,application/pdf,text/*"
                className="hidden"
                onChange={(event) => {
                  onFilesSelected(event.target.files);
                  event.target.value = "";
                }}
              />
            </label>
          </div>

          {entry.attachments.length === 0 ? (
            <p className="text-detail text-muted-foreground/50">No attachments yet.</p>
          ) : (
            <>
              {previewableAttachments.length > 0 && (
                <div className="flex flex-wrap gap-3">
                  {previewableAttachments.map((attachment) => (
                    <div key={attachment.id} className="relative group space-y-1">
                      <AttachmentPreview
                        key={attachment.id}
                        attachment={attachment}
                        onError={onAttachmentError}
                        onOpenAttachment={onOpenAttachment}
                      />
                      {attachment.kind === "image" && (
                        <button
                          type="button"
                          onClick={() => onUpdate({ coverAttachmentId: attachment.id })}
                          className="absolute top-1.5 left-1.5 opacity-0 group-hover:opacity-100 transition-opacity inline-flex items-center gap-1 rounded-lg bg-background/80 border border-foreground/[0.1] px-1.5 py-0.5 font-mono text-[8px] uppercase tracking-[0.12em] text-muted-foreground hover:text-foreground"
                        >
                          <StarGlyphIcon className="size-3" />
                          Set cover
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => onOpenAttachment(attachment)}
                        className="font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground/60 hover:text-foreground"
                      >
                        {attachment.filename || attachment.kind} · {formatFileSize(attachment.sizeBytes)}
                      </button>
                    </div>
                  ))}
                </div>
              )}
              {otherAttachments.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {otherAttachments.map((attachment) => {
                    const Icon = attachmentIcon(attachment.kind);
                    return (
                      <button
                        key={attachment.id}
                        type="button"
                        onClick={() => onOpenAttachment(attachment)}
                        className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-foreground/[0.08] bg-foreground/[0.03] px-2 py-1 text-caption text-muted-foreground hover:text-foreground hover:border-foreground/[0.15]"
                      >
                        <Icon size="sm" className="shrink-0" />
                        <span className="truncate">{attachment.filename || attachment.kind}</span>
                        <span className="font-mono text-[9px] text-muted-foreground/50">
                          {formatFileSize(attachment.sizeBytes)}
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </section>

        <section className="space-y-2">
          <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground/60">Voice note</p>
          <button
            type="button"
            onClick={onToggleRecording}
            className={cn(
              "w-full inline-flex items-center justify-center rounded-lg border px-3 py-2 text-[9px] uppercase tracking-[0.12em] font-mono transition-colors",
              recording
                ? "border-destructive/40 text-destructive bg-destructive/10"
                : "border-foreground/[0.08] bg-foreground/[0.03] text-muted-foreground hover:text-foreground hover:border-foreground/[0.15]",
            )}
          >
            <MicIcon size="sm" className="mr-2" />
            {recording ? "Stop recording" : "Record voice note"}
          </button>
          {recording && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-2.5 py-1.5 font-mono text-[9px] uppercase tracking-[0.16em] text-destructive">
              <span>{speechAvailable ? "Recording / transcribing" : "Recording"}</span>
              {liveTranscript && (
                <p className="mt-1 normal-case tracking-normal text-foreground/70">{liveTranscript}</p>
              )}
            </div>
          )}
        </section>

        <section className="space-y-1 border-t border-foreground/[0.08] pt-4">
          <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground/40">
            {wordCount} {wordCount === 1 ? "word" : "words"} · {charCount} {charCount === 1 ? "character" : "characters"}
          </p>
          <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground/40">
            Created {formatTimestamp(entry.createdAt)}
          </p>
          <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground/40">
            Updated {formatTimestamp(entry.updatedAt)}
          </p>
        </section>

        <section className="border-t border-foreground/[0.08] pt-4">
          <button
            type="button"
            onClick={onDelete}
            className="w-full rounded-lg border border-destructive/40 px-3 py-2 font-mono text-[9px] uppercase tracking-[0.14em] text-destructive hover:bg-destructive/10"
          >
            Delete entry
          </button>
        </section>
      </div>
    </aside>
  );
}
