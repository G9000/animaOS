import { useEffect, useState } from "react";
import { cn, ImageIcon, XIcon } from "@anima/standard-templates";
import type { DiaryAttachmentData, DiaryEntryData, DiaryFolderData } from "@anima/api-client";
import { api } from "../../../lib/api";
import type { SaveStatus } from "../lib/autosaveScheduler";
import { Glyph } from "../editor/glyphIcons";

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

function DrawerGlyphIcon({ className }: { className?: string } = {}) {
  return (
    <Glyph className={className}>
      <rect x="4" y="4" width="16" height="16" rx="0" />
      <path d="M14 4v16" />
    </Glyph>
  );
}

// Shared with the sidebar's entry-list thumbnail (DiaryWorkspace.tsx), which
// imports this hook back from here rather than duplicating it — this is now
// the single place a diary attachment's blob is fetched and turned into an
// object URL.
export function useAttachmentBlobUrl(
  attachment: { entryId: number; id: number } | null | undefined,
  onError?: (message: string) => void,
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
  }, [attachment?.entryId, attachment?.id, onError]);

  return url;
}

export function CoverBanner({
  attachment,
  onError,
}: {
  attachment: DiaryAttachmentData;
  onError: (message: string) => void;
}) {
  const url = useAttachmentBlobUrl(attachment, onError);

  if (!url) {
    return <div className="w-full h-56 rounded-xl bg-secondary/40 animate-pulse" />;
  }
  return <img src={url} alt="" className="w-full h-56 rounded-xl object-cover" />;
}

function saveStatusLabel(status: SaveStatus): string | null {
  switch (status) {
    case "saving":
      return "Saving…";
    case "saved":
      return "Saved";
    case "error":
      return "Needs attention";
    // "idle" covers both "nothing has happened yet" and — per the
    // scheduler's contract — "an edit was just scheduled, demoted down
    // from 'saved' so the UI never claims a clean save with unsent
    // edits". Neither case should read as "Saved", so render nothing
    // rather than inventing copy the brief didn't ask for.
    case "idle":
    default:
      return null;
  }
}

export interface PageHeaderProps {
  entry: DiaryEntryData;
  folders: DiaryFolderData[];
  saveStatus: SaveStatus;
  onRetry: () => void;
  onTitleChange: (title: string) => void;
  onToggleDrawer: () => void;
  drawerOpen: boolean;
  // Not in the brief's literal prop list, but "a properties row of date,
  // mood, and folder" has to be editable somewhere now that the read-mode
  // panel (and its Save button) is gone — these mirror the immediate-update
  // style already used elsewhere in DiaryWorkspace.tsx (e.g. moveEntryToFolder).
  onDateChange: (entryDate: string) => void;
  onMoodChange: (mood: string) => void;
  onFolderChange: (folderId: number | null) => void;
  onCoverFileSelected: (file: File) => void;
  onRemoveCover: () => void;
  onAttachmentError: (message: string) => void;
}

export function PageHeader({
  entry,
  folders,
  saveStatus,
  onRetry,
  onTitleChange,
  onToggleDrawer,
  drawerOpen,
  onDateChange,
  onMoodChange,
  onFolderChange,
  onCoverFileSelected,
  onRemoveCover,
  onAttachmentError,
}: PageHeaderProps) {
  // Local, uncommitted-per-keystroke copies of title/mood. The parent
  // mounts this component with `key={entry.id}` (see DiaryWorkspace.tsx),
  // so this state resets exactly when the user switches to a different
  // entry — never mid-typing just because an autosave response replaced
  // `entry` with a new (but content-equal) object.
  const [titleValue, setTitleValue] = useState(entry.title ?? "");
  const [moodValue, setMoodValue] = useState(entry.mood ?? "");

  const coverAttachment =
    entry.coverAttachmentId != null
      ? (entry.attachments.find((a) => a.id === entry.coverAttachmentId) ?? null)
      : null;

  const statusLabel = saveStatusLabel(saveStatus);

  return (
    <div>
      {coverAttachment ? (
        <div className="relative group">
          <CoverBanner attachment={coverAttachment} onError={onAttachmentError} />
          <button
            type="button"
            onClick={onRemoveCover}
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

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <label className="inline-flex items-center gap-1.5 rounded-full bg-foreground/[0.06] border border-foreground/[0.1] px-2.5 py-1 font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground/90 hover:text-foreground hover:border-foreground/[0.15] transition-colors">
          <CalendarGlyphIcon className="size-3.5" />
          <input
            type="date"
            value={entry.entryDate}
            onChange={(event) => onDateChange(event.target.value)}
            className="bg-transparent outline-none cursor-pointer"
          />
        </label>
        <input
          type="text"
          value={moodValue}
          onChange={(event) => setMoodValue(event.target.value)}
          onBlur={() => onMoodChange(moodValue)}
          onKeyDown={(event) => {
            if (event.key === "Enter") (event.target as HTMLInputElement).blur();
          }}
          placeholder="Mood"
          maxLength={80}
          className="rounded-full bg-foreground/[0.06] border border-foreground/[0.1] px-2.5 py-1 font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground/90 placeholder:text-muted-foreground/40 outline-none w-28 hover:border-foreground/[0.15] focus:border-accent/50 transition-colors"
        />
        {folders.length > 0 && (
          <label className="inline-flex items-center gap-1.5 rounded-full bg-foreground/[0.06] border border-foreground/[0.1] px-2.5 py-1 font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground/90 hover:text-foreground hover:border-foreground/[0.15] transition-colors">
            <FolderGlyphIcon className="size-3.5" />
            <select
              value={entry.folderId ?? ""}
              onChange={(event) =>
                onFolderChange(event.target.value ? Number(event.target.value) : null)
              }
              className="bg-transparent outline-none cursor-pointer max-w-28"
            >
              <option value="">No folder</option>
              {folders.map((folder) => (
                <option key={folder.id} value={folder.id}>
                  {folder.name}
                </option>
              ))}
            </select>
          </label>
        )}

        <div className="ml-auto flex items-center gap-2">
          {statusLabel && (
            <span
              className={cn(
                "font-mono text-[9px] tracking-[0.16em] uppercase",
                saveStatus === "error" ? "text-destructive" : "text-muted-foreground/50",
              )}
            >
              {statusLabel}
            </span>
          )}
          {saveStatus === "error" && (
            <button
              type="button"
              onClick={onRetry}
              className="font-mono text-[9px] uppercase tracking-[0.16em] text-destructive underline underline-offset-2 hover:text-destructive/80"
            >
              Retry
            </button>
          )}
          <button
            type="button"
            onClick={onToggleDrawer}
            title={drawerOpen ? "Hide details" : "Show details"}
            className={cn(
              "text-muted-foreground/50 hover:text-foreground",
              drawerOpen && "text-foreground",
            )}
          >
            <DrawerGlyphIcon />
          </button>
        </div>
      </div>

      <input
        type="text"
        value={titleValue}
        onChange={(event) => {
          setTitleValue(event.target.value);
          onTitleChange(event.target.value);
        }}
        placeholder="Untitled entry"
        maxLength={200}
        className="mt-6 w-full bg-transparent font-['Playfair_Display'] text-3xl md:text-4xl font-semibold tracking-tight text-foreground placeholder:text-muted-foreground/25 outline-none"
      />
    </div>
  );
}
