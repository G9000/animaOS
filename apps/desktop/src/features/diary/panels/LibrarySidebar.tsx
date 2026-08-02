import { useMemo, useState } from "react";
import {
  ChevronDownIcon,
  ChevronUpIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  PlusIcon,
  XIcon,
  cn,
} from "@anima/standard-templates";
import type { DiaryEntryData, DiaryFolderData } from "@anima/api-client";
import { filterDiaryEntries } from "../lib/entryFilter";
import { entryExcerpt, formatEntryDate, moodPillClass } from "../lib/textFormat";
import { Glyph } from "../editor/glyphIcons";
import { useAttachmentBlobUrl } from "../hooks/useAttachmentBlobUrl";

function PencilGlyphIcon({ className }: { className?: string } = {}) {
  return (
    <Glyph className={className}>
      <path d="M4 20l1-4.5L15.5 5 19 8.5 8.5 19 4 20Z" />
      <path d="M13 7l4 4" />
    </Glyph>
  );
}

function SearchGlyphIcon({ className }: { className?: string } = {}) {
  return (
    <Glyph className={className}>
      <circle cx="10.5" cy="10.5" r="6" />
      <path d="M15 15l5 5" />
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

function EntryCoverThumbnail({ entry }: { entry: DiaryEntryData }) {
  const cover =
    entry.coverAttachmentId != null
      ? (entry.attachments.find((a) => a.id === entry.coverAttachmentId) ?? null)
      : null;
  const url = useAttachmentBlobUrl(cover);

  if (!cover) return null;
  return (
    <div className="h-14 w-14 shrink-0 rounded-lg border border-foreground/[0.08] bg-secondary/40 overflow-hidden">
      {url && <img src={url} alt="" className="h-full w-full object-cover" />}
    </div>
  );
}

export interface LibrarySidebarProps {
  entries: DiaryEntryData[];
  folders: DiaryFolderData[];
  selectedId: number | null;
  query: string;
  activeFolderId: number | null;
  collapsed: boolean;
  onSelect: (id: number) => void;
  onQueryChange: (q: string) => void;
  onFolderChange: (id: number | null) => void;
  onCreate: () => void;
  onToggleCollapsed: () => void;
  // Deviations beyond the brief's literal prop list — needed so the
  // sidebar can render its own loading/empty/pagination/folder-CRUD
  // behavior (all pre-existing, just relocated) without the parent
  // re-implementing it. See task-12-report.md.
  loading: boolean;
  creatingEntry: boolean;
  deletingEntryId: number | null;
  onDeleteRequest: (entryId: number) => void;
  canLoadMore: boolean;
  onLoadMore: () => void;
  onCreateFolder: (name: string) => Promise<DiaryFolderData | null>;
  onRenameFolder: (folderId: number, name: string) => Promise<DiaryFolderData | null>;
  onDeleteFolder: (folderId: number) => Promise<boolean>;
}

export function LibrarySidebar({
  entries,
  folders,
  selectedId,
  query,
  activeFolderId,
  collapsed,
  onSelect,
  onQueryChange,
  onFolderChange,
  onCreate,
  onToggleCollapsed,
  loading,
  creatingEntry,
  deletingEntryId,
  onDeleteRequest,
  canLoadMore,
  onLoadMore,
  onCreateFolder,
  onRenameFolder,
  onDeleteFolder,
}: LibrarySidebarProps) {
  // Mood/date refinement filters are pure display concerns local to this
  // panel — nothing outside the sidebar reads them, unlike `query` and
  // `activeFolderId` (parent-owned: `activeFolderId` also picks the new
  // entry's folder in DiaryWorkspace's onCreate).
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [moodFilter, setMoodFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [isAddingFolder, setIsAddingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [editingFolderId, setEditingFolderId] = useState<number | null>(null);
  const [editingFolderName, setEditingFolderName] = useState("");
  const [pendingDeleteFolderId, setPendingDeleteFolderId] = useState<number | null>(null);

  const availableMoods = useMemo(() => {
    const moods = new Set<string>();
    for (const entry of entries) {
      if (entry.mood) moods.add(entry.mood);
    }
    return Array.from(moods).sort();
  }, [entries]);

  const filteredEntries = useMemo(
    () => filterDiaryEntries(entries, { query, activeFolderId, moodFilter, dateFrom, dateTo }),
    [entries, query, activeFolderId, moodFilter, dateFrom, dateTo],
  );

  const hasActiveFilters = Boolean(query || moodFilter || dateFrom || dateTo);

  const clearFilters = () => {
    onQueryChange("");
    setMoodFilter("");
    setDateFrom("");
    setDateTo("");
  };

  const startRenameFolder = (folder: DiaryFolderData) => {
    setEditingFolderId(folder.id);
    setEditingFolderName(folder.name);
  };

  const commitRenameFolder = async () => {
    const folderId = editingFolderId;
    const name = editingFolderName;
    setEditingFolderId(null);
    if (folderId == null) return;
    await onRenameFolder(folderId, name);
  };

  const commitCreateFolder = async () => {
    const name = newFolderName;
    setIsAddingFolder(false);
    setNewFolderName("");
    await onCreateFolder(name);
  };

  const confirmDeleteFolder = async () => {
    if (pendingDeleteFolderId == null) return;
    const folderId = pendingDeleteFolderId;
    setPendingDeleteFolderId(null);
    const deleted = await onDeleteFolder(folderId);
    if (deleted && activeFolderId === folderId) onFolderChange(null);
  };

  if (collapsed) {
    return (
      <aside className="w-14 shrink-0 rounded-xl border border-foreground/[0.08] bg-background/95 backdrop-blur-[36px] shadow-[0_4px_28px_rgba(0,0,0,0.18)] flex flex-col items-center gap-2 overflow-hidden py-3">
        <button
          type="button"
          onClick={onToggleCollapsed}
          title="Expand sidebar"
          className="h-8 w-8 flex items-center justify-center rounded-lg text-muted-foreground/60 hover:text-foreground hover:bg-foreground/[0.05]"
        >
          <ChevronRightIcon size="sm" />
        </button>
        <button
          type="button"
          onClick={onCreate}
          disabled={creatingEntry}
          title="New entry"
          className="h-9 w-9 flex items-center justify-center rounded-lg bg-accent text-accent-foreground shadow-[0_2px_10px_rgba(0,0,0,0.25)] hover:brightness-110 transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <PlusIcon size="sm" />
        </button>
        <div className="flex-1 w-full overflow-y-auto flex flex-col items-center gap-1.5 pt-1">
          {entries.map((entry) => (
            <button
              key={entry.id}
              type="button"
              onClick={() => onSelect(entry.id)}
              title={entry.title || formatEntryDate(entry.entryDate)}
              className={cn(
                "h-2 w-2 shrink-0 rounded-full transition-colors",
                selectedId === entry.id ? "bg-accent" : "bg-foreground/15 hover:bg-foreground/30",
              )}
            />
          ))}
        </div>
      </aside>
    );
  }

  return (
    <aside className="w-80 shrink-0 rounded-xl border border-foreground/[0.08] bg-background/95 backdrop-blur-[36px] shadow-[0_4px_28px_rgba(0,0,0,0.18)] flex flex-col overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-5 py-4">
        <div>
          <h1 className="font-['Playfair_Display'] text-2xl font-semibold text-foreground">Diary</h1>
          <p className="font-mono text-[9px] tracking-[0.22em] uppercase text-muted-foreground/50 mt-1">
            {entries.length} {entries.length === 1 ? "entry" : "entries"}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={onToggleCollapsed}
            title="Collapse sidebar"
            className="h-8 w-8 flex items-center justify-center rounded-lg text-muted-foreground/50 hover:text-foreground hover:bg-foreground/[0.05]"
          >
            <ChevronLeftIcon size="sm" />
          </button>
          <button
            type="button"
            onClick={onCreate}
            disabled={creatingEntry}
            title="New entry"
            className="h-9 w-9 flex items-center justify-center rounded-lg bg-accent text-accent-foreground shadow-[0_2px_10px_rgba(0,0,0,0.25)] hover:brightness-110 transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <PlusIcon size="sm" />
          </button>
        </div>
      </div>

      <div className="border-b border-foreground/[0.08] px-2 py-2 space-y-0.5">
        <button
          type="button"
          onClick={() => onFolderChange(null)}
          className={cn(
            "w-full flex items-center gap-2 rounded-lg px-3 py-1.5 text-left transition-colors",
            activeFolderId === null ? "bg-accent/15 text-accent" : "text-foreground hover:bg-foreground/[0.05]",
          )}
        >
          <FolderGlyphIcon className={activeFolderId === null ? "text-accent" : "text-muted-foreground/60"} />
          <span className="flex-1 truncate text-detail">All entries</span>
          <span className="font-mono text-[9px] text-muted-foreground/40">{entries.length}</span>
        </button>
        {folders.map((folder) => (
          <div key={folder.id} className="group relative flex items-center">
            {editingFolderId === folder.id ? (
              <input
                autoFocus
                value={editingFolderName}
                onChange={(event) => setEditingFolderName(event.target.value)}
                onBlur={() => void commitRenameFolder()}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void commitRenameFolder();
                  if (event.key === "Escape") setEditingFolderId(null);
                }}
                className="flex-1 mx-1 bg-foreground/[0.04] border border-foreground/[0.1] rounded-lg px-2 py-1 text-detail text-foreground outline-none focus:border-accent/50"
              />
            ) : (
              <button
                type="button"
                onClick={() => onFolderChange(folder.id)}
                className={cn(
                  "w-full flex items-center gap-2 rounded-lg pl-3 pr-16 py-1.5 text-left transition-colors",
                  activeFolderId === folder.id
                    ? "bg-accent/15 text-accent"
                    : "text-foreground hover:bg-foreground/[0.05]",
                )}
              >
                <FolderGlyphIcon
                  className={activeFolderId === folder.id ? "text-accent" : "text-muted-foreground/60"}
                />
                <span className="flex-1 truncate text-detail">{folder.name}</span>
                <span className="font-mono text-[9px] text-muted-foreground/40">{folder.entryCount}</span>
              </button>
            )}
            {editingFolderId !== folder.id && (
              <div className="absolute right-3 flex items-center gap-1.5 opacity-0 group-hover:opacity-100">
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    startRenameFolder(folder);
                  }}
                  title="Rename folder"
                  className="text-muted-foreground/50 hover:text-foreground"
                >
                  <PencilGlyphIcon className="size-3.5" />
                </button>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    setPendingDeleteFolderId(folder.id);
                  }}
                  title="Delete folder"
                  className="text-muted-foreground/50 hover:text-destructive"
                >
                  <XIcon size="sm" />
                </button>
              </div>
            )}
          </div>
        ))}
        {isAddingFolder ? (
          <input
            autoFocus
            value={newFolderName}
            onChange={(event) => setNewFolderName(event.target.value)}
            onBlur={() => void commitCreateFolder()}
            onKeyDown={(event) => {
              if (event.key === "Enter") void commitCreateFolder();
              if (event.key === "Escape") {
                setIsAddingFolder(false);
                setNewFolderName("");
              }
            }}
            placeholder="Folder name"
            className="mx-1 bg-foreground/[0.04] border border-foreground/[0.1] rounded-lg px-2 py-1 text-detail text-foreground placeholder:text-muted-foreground/40 outline-none focus:border-accent/50"
          />
        ) : (
          <button
            type="button"
            onClick={() => setIsAddingFolder(true)}
            className="w-full flex items-center gap-2 rounded-lg px-3 py-1.5 text-left font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground/60 hover:text-foreground hover:bg-foreground/[0.05]"
          >
            <PlusIcon size="sm" />
            New folder
          </button>
        )}
      </div>

      <div className="px-3 py-2.5 border-b border-foreground/[0.08] space-y-2">
        <div className="relative">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground/40 pointer-events-none">
            <SearchGlyphIcon />
          </span>
          <input
            type="text"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="Search entries…"
            className="w-full bg-foreground/[0.04] border border-foreground/[0.08] rounded-lg pl-9 pr-2 py-1.5 text-detail text-foreground placeholder:text-muted-foreground/40 outline-none focus:border-accent/50 transition-colors"
          />
        </div>
        <div className="flex items-center gap-3 px-1">
          <button
            type="button"
            onClick={() => setFiltersOpen((open) => !open)}
            className="inline-flex items-center gap-1 font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground hover:text-foreground"
          >
            {filtersOpen ? "Hide filters" : "Filters"}
            {filtersOpen ? <ChevronUpIcon size="sm" /> : <ChevronDownIcon size="sm" />}
          </button>
          {hasActiveFilters && (
            <button
              type="button"
              onClick={clearFilters}
              className="font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground/60 hover:text-destructive"
            >
              Clear
            </button>
          )}
          {hasActiveFilters && (
            <span className="ml-auto font-mono text-[9px] text-muted-foreground/40">
              {filteredEntries.length}/{entries.length}
            </span>
          )}
        </div>
        {filtersOpen && (
          <div className="space-y-1.5 pt-0.5 animate-fade-in">
            <select
              value={moodFilter}
              onChange={(event) => setMoodFilter(event.target.value)}
              className="w-full bg-foreground/[0.04] border border-foreground/[0.08] rounded-lg px-2 py-1 text-[10px] text-muted-foreground outline-none"
            >
              <option value="">All moods</option>
              {availableMoods.map((moodOption) => (
                <option key={moodOption} value={moodOption}>
                  {moodOption}
                </option>
              ))}
            </select>
            <div className="flex items-center gap-1.5">
              <input
                type="date"
                value={dateFrom}
                onChange={(event) => setDateFrom(event.target.value)}
                className="flex-1 min-w-0 bg-foreground/[0.04] border border-foreground/[0.08] rounded-lg px-1.5 py-1 text-[10px] text-muted-foreground outline-none"
              />
              <span className="text-muted-foreground/30">–</span>
              <input
                type="date"
                value={dateTo}
                onChange={(event) => setDateTo(event.target.value)}
                className="flex-1 min-w-0 bg-foreground/[0.04] border border-foreground/[0.08] rounded-lg px-1.5 py-1 text-[10px] text-muted-foreground outline-none"
              />
            </div>
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-2 py-2">
        {loading ? (
          <div className="flex items-center gap-1.5 py-12 justify-center">
            <span className="w-1 h-1 bg-muted-foreground/40 animate-pulse" />
            <span className="w-1 h-1 bg-muted-foreground/40 animate-pulse [animation-delay:150ms]" />
            <span className="w-1 h-1 bg-muted-foreground/40 animate-pulse [animation-delay:300ms]" />
          </div>
        ) : entries.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-4 py-10 text-muted-foreground/30">
            <PencilGlyphIcon className="size-5" />
            <p className="text-center font-mono text-[10px] tracking-[0.2em] uppercase text-muted-foreground/40">
              No entries yet
            </p>
            <button
              type="button"
              onClick={onCreate}
              disabled={creatingEntry}
              className="mt-1 font-mono text-[9px] uppercase tracking-[0.18em] text-accent hover:text-accent/80 disabled:opacity-50"
            >
              Create your first entry
            </button>
          </div>
        ) : filteredEntries.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-4 py-10 text-muted-foreground/30">
            <SearchGlyphIcon />
            <p className="text-center font-mono text-[10px] tracking-[0.2em] uppercase text-muted-foreground/40">
              No matching entries
            </p>
            <button
              type="button"
              onClick={clearFilters}
              className="mt-1 font-mono text-[9px] uppercase tracking-[0.18em] text-accent hover:text-accent/80"
            >
              Clear filters
            </button>
          </div>
        ) : (
          <ul className="space-y-1">
            {filteredEntries.map((entry) => (
              <li key={entry.id}>
                <button
                  type="button"
                  onClick={() => onSelect(entry.id)}
                  className={cn(
                    "group/entry w-full text-left rounded-lg px-3 py-2.5 transition-all",
                    selectedId === entry.id
                      ? "bg-accent/12 ring-1 ring-accent/30"
                      : "hover:bg-foreground/[0.04]",
                  )}
                >
                  <div className="flex items-start gap-3">
                    <EntryCoverThumbnail entry={entry} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[9px] tracking-[0.2em] uppercase text-muted-foreground/75">
                          {formatEntryDate(entry.entryDate)}
                        </span>
                        {entry.mood && (
                          <span
                            className={cn(
                              "rounded-full border px-1.5 py-0 text-[9px] uppercase tracking-[0.08em] truncate",
                              moodPillClass(entry.mood),
                            )}
                          >
                            {entry.mood}
                          </span>
                        )}
                        {entry.attachments.length > 0 && (
                          <span className="ml-auto font-mono text-[9px] text-muted-foreground/40 shrink-0">
                            {entry.attachments.length} ⊕
                          </span>
                        )}
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            onDeleteRequest(entry.id);
                          }}
                          disabled={deletingEntryId === entry.id}
                          title="Delete entry"
                          className="opacity-0 group-hover/entry:opacity-100 inline-flex items-center justify-center h-6 w-6 rounded-full text-muted-foreground/50 hover:text-destructive hover:bg-destructive/10 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
                        >
                          <XIcon size="sm" />
                        </button>
                      </div>
                      {entry.title && (
                        <p className="mt-1 text-body font-medium text-foreground truncate">{entry.title}</p>
                      )}
                      <p className="mt-0.5 text-detail text-muted-foreground line-clamp-2">
                        {entryExcerpt(entry)}
                      </p>
                    </div>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
        {!loading && canLoadMore && (
          <button
            type="button"
            onClick={onLoadMore}
            className="w-full mt-1 py-2.5 rounded-lg font-mono text-[9px] uppercase tracking-[0.18em] text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
          >
            Load more
          </button>
        )}
      </div>

      {pendingDeleteFolderId != null && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-background/60 backdrop-blur-sm"
          onClick={() => setPendingDeleteFolderId(null)}
        >
          <div
            className="rounded-xl border border-foreground/[0.1] bg-card px-6 py-5 max-w-sm w-full mx-4 shadow-xl animate-fade-in"
            onClick={(event) => event.stopPropagation()}
          >
            <p className="text-body text-foreground">Delete this folder?</p>
            <p className="mt-1 text-detail text-muted-foreground">
              Entries stay — they're just unfiled, not deleted.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setPendingDeleteFolderId(null)}
                className="rounded-lg border border-foreground/[0.08] px-3 py-1.5 font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground hover:text-foreground hover:border-foreground/[0.15]"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void confirmDeleteFolder()}
                className="rounded-lg border border-destructive/40 px-3 py-1.5 font-mono text-[9px] uppercase tracking-[0.12em] text-destructive hover:bg-destructive/10"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}
