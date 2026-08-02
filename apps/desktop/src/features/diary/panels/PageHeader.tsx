import { useState } from "react";
import { cn } from "@anima/standard-templates";
import type { DiaryEntryData } from "@anima/api-client";
import type { SaveStatus } from "../lib/autosaveScheduler";
import { Glyph } from "../editor/glyphIcons";

function DrawerGlyphIcon({ className }: { className?: string } = {}) {
  return (
    <Glyph className={className}>
      <rect x="4" y="4" width="16" height="16" rx="0" />
      <path d="M14 4v16" />
    </Glyph>
  );
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
  saveStatus: SaveStatus;
  onRetry: () => void;
  onTitleChange: (title: string) => void;
  onToggleDrawer: () => void;
  drawerOpen: boolean;
}

// Task 12: date/mood/folder/cover controls moved out of this component into
// panels/DetailsDrawer.tsx (Step 3 gives them a real home; Task 11's report
// flagged them as an interim stand-in living here only because there was
// nowhere else for them yet). What remains is exactly the brief's original
// Task 11 scope: title, save status, and the drawer toggle.
export function PageHeader({
  entry,
  saveStatus,
  onRetry,
  onTitleChange,
  onToggleDrawer,
  drawerOpen,
}: PageHeaderProps) {
  // Local, uncommitted-per-keystroke copy of title. The parent mounts this
  // component with `key={entry.id}` (see DiaryWorkspace.tsx), so this state
  // resets exactly when the user switches to a different entry — never
  // mid-typing just because an autosave response replaced `entry` with a
  // new (but content-equal) object.
  const [titleValue, setTitleValue] = useState(entry.title ?? "");

  const statusLabel = saveStatusLabel(saveStatus);

  return (
    <div>
      <div className="flex items-center justify-end gap-2">
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
          className={cn("text-muted-foreground/50 hover:text-foreground", drawerOpen && "text-foreground")}
        >
          <DrawerGlyphIcon />
        </button>
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
        className="mt-2 w-full bg-transparent font-['Playfair_Display'] text-3xl md:text-4xl font-semibold tracking-tight text-foreground placeholder:text-muted-foreground/25 outline-none"
      />
    </div>
  );
}
