import { useEffect, useMemo, useRef, useState } from "react";
import { FileIcon } from "@anima/standard-templates";
import type {
  DiaryAttachmentData,
  DiaryEntryData,
  DiaryEntryUpdateData,
} from "@anima/api-client";
import type { Editor } from "@tiptap/react";
import { useAuth } from "../../context/AuthContext";
import { createDiaryHtmlSanitizer } from "./lib/sanitize";
import {
  hasNonTextNode,
  isDiscardablePage,
  isSignificantEdit,
  resolveBodyForSave,
  snapshotBelongsToEntry,
} from "./lib/pageLifecycle";
import { isHtmlBody, escapeHtmlForEditor, formatFileSize } from "./lib/textFormat";
import { handleInstanceTornDown } from "./lib/editorHandoff";
import { Glyph } from "./editor/glyphIcons";
import { DiaryEditor } from "./editor/DiaryEditor";
import { useAutosave } from "./hooks/useAutosave";
import { useDiaryEntries } from "./hooks/useDiaryEntries";
import { useVoiceRecorder } from "./hooks/useVoiceRecorder";
import { PageHeader } from "./panels/PageHeader";
import { LibrarySidebar } from "./panels/LibrarySidebar";
import { DetailsDrawer } from "./panels/DetailsDrawer";

const MAX_INLINE_IMAGE_BYTES = 3 * 1024 * 1024;

const sanitizeDiaryHtml = createDiaryHtmlSanitizer(window);

function PencilGlyphIcon({ className }: { className?: string }) {
  return (
    <Glyph className={className}>
      <path d="M4 20l1-4.5L15.5 5 19 8.5 8.5 19 4 20Z" />
      <path d="M13 7l4 4" />
    </Glyph>
  );
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read file."));
    reader.readAsDataURL(file);
  });
}

// The one place the editor's ProseMirror doc is walked to decide whether it
// carries content that isn't plain text. Delegates the actual "does this
// set of node names count as non-text" decision to pageLifecycle's
// `hasNonTextNode`, which is pure and unit-tested against plain string
// lists — this function is just the Tiptap-specific adapter that collects
// the node names to feed it. See the CAUTION comment on isDiscardablePage
// for why this matters: bodyPlainText alone is "" for a page whose only
// content is an image, an empty table, a divider, or an empty
// callout/details/task item.
function editorHasNonTextContent(ed: Editor): boolean {
  const nodeTypeNames = new Set<string>();
  ed.state.doc.descendants((node) => {
    nodeTypeNames.add(node.type.name);
  });
  return hasNonTextNode(nodeTypeNames);
}

export default function DiaryWorkspace() {
  const { user } = useAuth();
  const {
    entries,
    folders,
    loading,
    error,
    setError,
    canLoadMore,
    loadMore,
    createEntry,
    updateEntry,
    deleteEntry,
    discardEntrySilently,
    moveEntryToFolder,
    uploadAttachment,
    saveEntryFields,
    downloadAttachment,
    createFolder,
    renameFolder,
    deleteFolder,
  } = useDiaryEntries(user?.id ?? null);

  const [creatingEntry, setCreatingEntry] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [bodyText, setBodyText] = useState("");
  // Drawer-open and sidebar-collapsed state live in component state only —
  // never in browser storage (diary content and layout preferences never
  // touch localStorage/sessionStorage; see the legacy-draft purge effect
  // below).
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [deletingEntryId, setDeletingEntryId] = useState<number | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [activeFolderId, setActiveFolderId] = useState<number | null>(null);
  const [isDraggingFile, setIsDraggingFile] = useState(false);
  const editorWrapperRef = useRef<HTMLDivElement | null>(null);
  const hiddenImageInputRef = useRef<HTMLInputElement | null>(null);
  const editorRef = useRef<Editor | null>(null);

  // Kept fresh every render (not via an effect) so that plain React effect
  // cleanups — which otherwise close over stale values from whenever they
  // were registered — always see the latest entry/title/content snapshot.
  const selectedEntryRef = useRef<DiaryEntryData | null>(null);
  const titleRef = useRef("");
  // The last body HTML this component itself fed INTO the editor via its
  // `initialHtml` prop (or that the user's own edit last advanced it to —
  // see handleEditorChange). Compared against onChange's output as a
  // defense-in-depth guard against a save loop, on top of DiaryEditor
  // mounting with fresh `content` per entry (which never re-applies
  // reactively — see editor/DiaryEditor.tsx).
  const lastLoadedBodyRef = useRef<string | null>(null);
  // Kept in sync on every edit and on every fresh editor mount (see
  // handleEditorReady) so the untitled-page cleanup can read a fresh
  // snapshot without ever touching a possibly-already-destroyed editor
  // instance from an unmount cleanup.
  //
  // Task 12 review, Finding 2: `entryId` tags WHICH entry this snapshot
  // describes. DiaryEditor's `create` (and therefore the first real
  // syncEditorContent call for a newly selected entry, via
  // handleEditorReady) can land a macrotask after the entry switch itself
  // — confirmed against the installed @tiptap/react sources. If the user
  // switches away again before that `create` fires,
  // `lastContentSnapshotRef` would otherwise still hold data belonging to
  // a DIFFERENT entry, and evaluateAndMaybeDiscard must never evaluate one
  // entry's discardability against another entry's content. Tagging the
  // data with the entry id it was captured from, and having
  // evaluateAndMaybeDiscard refuse to trust a mismatched tag, makes this
  // safe regardless of timing: the guard doesn't care WHEN the snapshot
  // was written, only WHETHER it is tagged as belonging to the entry being
  // evaluated.
  //
  // `hasNonTextContent` defaults to `true`, not `false` (Finding 1b):
  // "unavailable" must fail toward "keep the page", never toward "looks
  // discardable" — the same reasoning applies to every input that feeds
  // isDiscardablePage.
  const lastContentSnapshotRef = useRef<{
    entryId: number | null;
    bodyPlainText: string;
    hasNonTextContent: boolean;
  }>({
    entryId: null,
    bodyPlainText: "",
    hasNonTextContent: true,
  });

  const selectedEntry = useMemo(
    () => entries.find((entry) => entry.id === selectedId) ?? null,
    [entries, selectedId],
  );
  selectedEntryRef.current = selectedEntry;

  // Sanitized HTML handed to DiaryEditor as its `initialHtml` prop. Memoized
  // on the entry id alone (not the whole entry, and not entry.body) so an
  // autosave echo replacing `entries` mid-typing never produces a "new"
  // value here — DiaryEditor is keyed by entry.id and only reads this prop
  // once, at construction, but keeping it referentially stable is cheap
  // insurance regardless.
  const initialHtml = useMemo(() => {
    if (!selectedEntry) return "";
    return sanitizeDiaryHtml(
      isHtmlBody(selectedEntry.body) ? selectedEntry.body : escapeHtmlForEditor(selectedEntry.body),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedEntry?.id]);

  const syncEditorContent = (ed: Editor, entryId: number) => {
    const plainText = ed.getText();
    setBodyText(plainText);
    lastContentSnapshotRef.current = {
      entryId,
      bodyPlainText: plainText,
      hasNonTextContent: editorHasNonTextContent(ed),
    };
  };

  // Fired by DiaryEditor's own onCreate (see the doc comment on
  // DiaryEditorProps.onEditorReady — always a live, non-null instance).
  // Reproduces the pre-Task-12 `editorRef.current = editor` assignment,
  // plus — because DiaryEditor now mounts with `content: initialHtml` as a
  // construction option rather than an imperative `setContent` call, which
  // never fires `onUpdate` — this is the only place a freshly mounted
  // editor's real doc state (word count, non-text-content snapshot) gets
  // captured for the untitled-page cleanup.
  const handleEditorReady = (ed: Editor, entryId: number) => {
    editorRef.current = ed;
    syncEditorContent(ed, entryId);
  };

  // Task 12 review, Finding 1: identity-aware teardown, factored into the
  // pure (and unit-tested — see tests/diary-editor-handoff.test.ts)
  // `handleInstanceTornDown`. `ed` here is the SAME instance DiaryEditor
  // captured in its own onCreate — not necessarily "whatever is currently
  // selected".
  const handleEditorDestroyed = (ed: Editor) => {
    handleInstanceTornDown(editorRef, ed);
  };

  // Reset the refs the autosave/discard machinery reads whenever the
  // selected entry changes. DiaryEditor (keyed by entry.id) handles loading
  // the new content itself via `initialHtml`; this only resets the
  // bookkeeping refs to match.
  useEffect(() => {
    if (!selectedEntry) return;
    lastLoadedBodyRef.current = initialHtml;
    titleRef.current = selectedEntry.title ?? "";
  }, [selectedEntry?.id, initialHtml]);

  // Remove drafts written by older builds. Diary content must stay in the
  // encrypted diary service, not in browser storage.
  useEffect(() => {
    if (user?.id == null) return;
    const prefix = `anima:diary:draft:${user.id}:`;
    try {
      for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
        const key = window.localStorage.key(index);
        if (key?.startsWith(prefix)) window.localStorage.removeItem(key);
      }
    } catch {
      // Browser storage can be unavailable in restricted environments.
    }
  }, [user?.id]);

  // Voice-note recording + live speech-to-text, extracted (Task 12) into
  // its own hook — see hooks/useVoiceRecorder.ts. The two callbacks below
  // are the only points that used to reach directly into this component's
  // state; they fire at the exact same moments (recorder.onstop,
  // recognition.onresult) as the pre-extraction inline code did.
  const voiceRecorder = useVoiceRecorder({
    onFinalTranscript: (text) => {
      const ed = editorRef.current;
      const entryId = selectedEntryRef.current?.id;
      if (ed && entryId != null) {
        ed.commands.insertContentAt(ed.state.doc.content.size, text);
        syncEditorContent(ed, entryId);
      }
    },
    onRecordingComplete: (file) => {
      const entryId = selectedEntryRef.current?.id;
      if (entryId != null) void uploadAttachment(entryId, file);
    },
    onError: setError,
  });

  // --- Autosave -------------------------------------------------------
  //
  // `save` is a fresh closure every render, but the scheduler it feeds is
  // only (re)created when `entryId` changes (see useAutosave's doc
  // comment) — so the entry id this closure captures via `selectedEntry`
  // is frozen for the scheduler's whole lifetime, and a save queued
  // against one entry can never be redirected to whatever entry is
  // selected by the time it actually flushes.
  const autosaveEntryId = selectedEntry?.id ?? null;
  const {
    schedule,
    flush,
    retry,
    status: saveStatus,
  } = useAutosave<{ title: string; body: string }>({
    entryId: autosaveEntryId,
    save: async (payload) => {
      const entryId = autosaveEntryId;
      if (entryId == null) return;
      // saveEntryFields is deliberately raw (throws on failure) — see its
      // doc comment in hooks/useDiaryEntries.ts. The scheduler's own
      // try/catch (lib/autosaveScheduler.ts `run()`) is what turns a
      // rejection into "error" status and arms retry().
      await saveEntryFields(entryId, payload);
    },
  });

  // A page the user created but never touched (per lib/pageLifecycle.ts).
  // Always flushes any pending autosave first so the decision is made
  // against fully up-to-date state, then deletes if still discardable.
  // Never call this against the entry the user is currently editing —
  // only against the one being left (a switch target, or on unmount).
  //
  // Fix round 1, Finding 3 (Task 11): on the true-unmount path this
  // `flush()` call is only a real barrier because useAutosave's own
  // teardown does not null its scheduler ref before flushing (see the
  // comment in hooks/useAutosave.ts) — `flush` here reaches the live
  // scheduler for whichever entry is being left regardless of which of
  // the two unmount-time cleanups (this one, or useAutosave's own) happens
  // to run first, rather than silently resolving instantly against a null
  // ref. That invariant cost a fix round to establish; it still holds
  // here unchanged.
  const evaluateAndMaybeDiscard = async (entry: DiaryEntryData) => {
    await flush();
    const snapshot = lastContentSnapshotRef.current;
    // Task 12 review, Finding 2: refuse to evaluate against a snapshot
    // that isn't tagged as belonging to THIS entry — see the doc comment
    // on lastContentSnapshotRef. No snapshot yet for this entry is treated
    // the same as Finding 1b's fail-safe direction: keep the page rather
    // than delete on unverified grounds.
    if (!snapshotBelongsToEntry(snapshot.entryId, entry.id)) return;
    const discardable = isDiscardablePage({
      title: titleRef.current,
      bodyPlainText: snapshot.bodyPlainText,
      attachmentCount: entry.attachments.length,
      coverAttachmentId: entry.coverAttachmentId,
      hasNonTextContent: snapshot.hasNonTextContent,
    });
    if (!discardable) return;
    await discardEntrySilently(entry.id);
  };

  // Untitled-page cleanup on true component unmount (e.g. navigating away
  // from the diary route entirely). The per-entry-switch case is handled
  // imperatively in selectEntry/startNewEntry, below.
  useEffect(() => {
    return () => {
      const entry = selectedEntryRef.current;
      if (entry) void evaluateAndMaybeDiscard(entry);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Called by DiaryEditor's onChange (see editor/DiaryEditor.tsx). This is
  // the relocated body of the old inline `useEditor({ onUpdate })` handler
  // — same significant-edit gate, same lastLoadedBodyRef advance, same
  // resolveBodyForSave + schedule call, just now reached through a prop
  // instead of a closure inside useEditor's options.
  const handleEditorChange = (html: string, plainText: string) => {
    setBodyText(plainText);
    const ed = editorRef.current;
    const entry = selectedEntryRef.current;
    lastContentSnapshotRef.current = {
      entryId: entry?.id ?? null,
      bodyPlainText: plainText,
      // Finding 1b (fail-safe): a missing editor must never make a page
      // look discardable — "unavailable" defaults to "assume it might
      // carry non-text content", not "assume it doesn't".
      hasNonTextContent: ed ? editorHasNonTextContent(ed) : true,
    };

    if (!entry) return;

    // Fix round 1, Finding 1 (CRITICAL, Task 11): only skip a true no-op
    // (current output identical to what was last loaded/scheduled) — an
    // intentional clear-all-text produces different output and is always
    // scheduled, with an explicit blank body (see resolveBodyForSave).
    if (!isSignificantEdit({ loadedHtml: lastLoadedBodyRef.current ?? "", currentHtml: html })) {
      return;
    }
    // Advance the reference point to this edit's output — without this,
    // reverting to the pristine loaded state after an intermediate save
    // would never be recognized as a real edit needing its own save.
    lastLoadedBodyRef.current = html;

    const body = resolveBodyForSave({
      editorIsEmpty: ed ? ed.isEmpty : plainText.trim() === "",
      editorHtml: html,
      plainText,
      attachmentCount: entry.attachments.length,
    });
    schedule({ title: titleRef.current, body });
  };

  const handleTitleChange = (newTitle: string) => {
    titleRef.current = newTitle;
    const ed = editorRef.current;
    const entry = selectedEntryRef.current;
    if (!ed || !entry) return;
    // No eligibility gate here (fix round 1, Finding 1, Task 11):
    // PageHeader's title input only calls this from its own onChange,
    // which only fires on a genuine keystroke.
    const html = sanitizeDiaryHtml(ed.getHTML());
    const plainText = ed.getText();
    const body = resolveBodyForSave({
      editorIsEmpty: ed.isEmpty,
      editorHtml: html,
      plainText,
      attachmentCount: entry.attachments.length,
    });
    schedule({ title: newTitle, body });
  };

  const insertInlineImage = async (file: File) => {
    if (!file.type.startsWith("image/")) return;
    if (file.size > MAX_INLINE_IMAGE_BYTES) {
      setError(
        `"${file.name}" is too large to embed inline (max ${formatFileSize(MAX_INLINE_IMAGE_BYTES)}). Use Attach for large files instead.`,
      );
      return;
    }
    try {
      const dataUrl = await fileToDataUrl(file);
      editorRef.current?.chain().focus().setImage({ src: dataUrl, alt: file.name }).run();
      const entryId = selectedEntryRef.current?.id;
      if (editorRef.current && entryId != null) syncEditorContent(editorRef.current, entryId);
    } catch {
      setError(`Failed to embed "${file.name}".`);
    }
  };

  const handleInlineImageFilesSelected = (selected: FileList | null) => {
    if (!selected || selected.length === 0) return;
    for (const file of Array.from(selected)) {
      void insertInlineImage(file);
    }
  };

  const startNewEntry = async () => {
    if (user?.id == null || creatingEntry) return;
    const leaving = selectedEntryRef.current;
    voiceRecorder.stopIfActive();
    setCreatingEntry(true);
    try {
      if (leaving) await evaluateAndMaybeDiscard(leaving);
      const created = await createEntry({ folderId: activeFolderId });
      if (created) setSelectedId(created.id);
    } finally {
      setCreatingEntry(false);
    }
  };

  const selectEntry = async (entryId: number) => {
    if (entryId === selectedId) return;
    const leaving = selectedEntryRef.current;
    voiceRecorder.stopIfActive();
    if (leaving) await evaluateAndMaybeDiscard(leaving);
    setSelectedId(entryId);
  };

  const handleFilesSelected = (selected: FileList | null) => {
    if (!selected || selected.length === 0 || !selectedEntry) return;
    const entryId = selectedEntry.id;
    for (const file of Array.from(selected)) {
      void uploadAttachment(entryId, file);
    }
  };

  const handleComposerDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    setIsDraggingFile(true);
  };

  const handleComposerDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setIsDraggingFile(false);
  };

  const handleComposerDrop = (event: React.DragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    setIsDraggingFile(false);
    handleFilesSelected(event.dataTransfer.files);
  };

  const handleDelete = async (entryId: number) => {
    if (deletingEntryId !== null) return;
    setDeletingEntryId(entryId);
    setError(null);
    const success = await deleteEntry(entryId);
    if (success && selectedId === entryId) setSelectedId(null);
    setDeletingEntryId(null);
  };

  const confirmDelete = async () => {
    if (pendingDeleteId == null) return;
    const entryId = pendingDeleteId;
    setPendingDeleteId(null);
    await handleDelete(entryId);
  };

  const handleOpenAttachment = async (attachment: DiaryAttachmentData) => {
    try {
      const blob = await downloadAttachment(attachment.entryId, attachment.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noreferrer";
      if (attachment.kind === "file" && attachment.filename) {
        link.download = attachment.filename;
      }
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to open attachment.");
    }
  };

  const handleCoverFileSelected = async (file: File) => {
    if (!selectedEntry) return;
    const uploaded = await uploadAttachment(selectedEntry.id, file, "Failed to set cover image.");
    if (!uploaded) return;
    await updateEntry(selectedEntry.id, { coverAttachmentId: uploaded.id }, "Failed to set cover image.");
  };

  // Dispatches DetailsDrawer's single generic `onUpdate` to the right
  // hook call with the right pre-existing error message (point 7) — folder
  // changes specifically need moveEntryToFolder (which also refreshes
  // folder entry counts), not the generic updateEntry.
  const handleDrawerUpdate = (data: DiaryEntryUpdateData) => {
    const entry = selectedEntryRef.current;
    if (!entry) return;
    if ("folderId" in data || "clearFolder" in data) {
      void moveEntryToFolder(entry.id, data.folderId ?? null);
      return;
    }
    if ("entryDate" in data) {
      void updateEntry(entry.id, data, "Failed to update the entry date.");
      return;
    }
    if ("mood" in data || "clearMood" in data) {
      void updateEntry(entry.id, data, "Failed to update mood.");
      return;
    }
    if ("clearCover" in data) {
      void updateEntry(entry.id, data, "Failed to remove cover image.");
      return;
    }
    if ("coverAttachmentId" in data) {
      void updateEntry(entry.id, data, "Failed to set cover image.");
      return;
    }
    void updateEntry(entry.id, data);
  };

  return (
    <div className="h-full pt-16 p-4 flex gap-4 overflow-hidden">
      <LibrarySidebar
        entries={entries}
        folders={folders}
        selectedId={selectedId}
        query={searchQuery}
        activeFolderId={activeFolderId}
        collapsed={sidebarCollapsed}
        onSelect={(id) => void selectEntry(id)}
        onQueryChange={setSearchQuery}
        onFolderChange={setActiveFolderId}
        onCreate={() => void startNewEntry()}
        onToggleCollapsed={() => setSidebarCollapsed((collapsed) => !collapsed)}
        loading={loading}
        creatingEntry={creatingEntry}
        deletingEntryId={deletingEntryId}
        onDeleteRequest={setPendingDeleteId}
        canLoadMore={canLoadMore}
        onLoadMore={loadMore}
        onCreateFolder={createFolder}
        onRenameFolder={renameFolder}
        onDeleteFolder={deleteFolder}
      />

      {/* Canvas — always editable */}
      <main className="flex-1 min-w-0 rounded-xl border border-foreground/[0.08] bg-background/95 backdrop-blur-[36px] shadow-[0_4px_28px_rgba(0,0,0,0.18)] flex flex-col overflow-hidden">
        {error && (
          <div className="mx-8 mt-4 border border-destructive/30 bg-destructive/10 px-3 py-2 text-detail text-destructive animate-fade-in">
            {error}
          </div>
        )}

        {selectedEntry ? (
          <div className="flex-1 min-h-0 flex flex-col">
            <div className="flex-1 min-h-0 overflow-y-auto">
              <div className="max-w-3xl mx-auto px-8 pt-10 pb-4 h-full flex flex-col">
                <PageHeader
                  key={selectedEntry.id}
                  entry={selectedEntry}
                  saveStatus={saveStatus}
                  onRetry={() => void retry()}
                  onTitleChange={handleTitleChange}
                  onToggleDrawer={() => setDrawerOpen((open) => !open)}
                  drawerOpen={drawerOpen}
                />

                <div
                  ref={editorWrapperRef}
                  className="diary-editor-shell relative mt-4 flex-1 min-h-[40vh] cursor-text"
                  onClick={() => editorRef.current?.commands.focus()}
                  onDragOver={handleComposerDragOver}
                  onDragLeave={handleComposerDragLeave}
                  onDrop={handleComposerDrop}
                >
                  <DiaryEditor
                    key={selectedEntry.id}
                    entryId={selectedEntry.id}
                    initialHtml={initialHtml}
                    onChange={handleEditorChange}
                    onImageRequest={() => hiddenImageInputRef.current?.click()}
                    onImagePaste={async (file) => {
                      const entryId = selectedEntryRef.current?.id;
                      if (entryId == null) return null;
                      const uploaded = await uploadAttachment(
                        entryId,
                        file,
                        `Failed to attach "${file.name}".`,
                      );
                      return uploaded?.id ?? null;
                    }}
                    onEditorReady={handleEditorReady}
                    onEditorDestroyed={handleEditorDestroyed}
                  />
                  {isDraggingFile && (
                    <div className="absolute inset-0 z-30 flex items-center justify-center rounded-xl border-2 border-dashed border-accent/60 bg-background/80 pointer-events-none">
                      <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-accent">
                        Drop to attach
                      </p>
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* Bottom toolbar */}
            <div className="border-t border-foreground/[0.08]">
              <div className="max-w-3xl mx-auto px-8 py-3">
                <div className="flex items-center gap-2">
                  <label className="inline-flex items-center rounded-lg border border-foreground/[0.08] bg-foreground/[0.03] cursor-pointer px-3 py-2 text-[9px] uppercase tracking-[0.12em] font-mono text-muted-foreground hover:text-foreground hover:border-foreground/[0.15] transition-colors">
                    <FileIcon size="sm" className="mr-2" />
                    Attach
                    <input
                      type="file"
                      multiple
                      accept="image/*,audio/*,video/*,application/pdf,text/*"
                      className="hidden"
                      onChange={(event) => {
                        handleFilesSelected(event.target.files);
                        event.target.value = "";
                      }}
                    />
                  </label>
                  <input
                    ref={hiddenImageInputRef}
                    type="file"
                    multiple
                    accept="image/*"
                    className="hidden"
                    onChange={(event) => {
                      handleInlineImageFilesSelected(event.target.files);
                      event.target.value = "";
                    }}
                  />
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center">
            <div className="flex flex-col items-center gap-2 text-muted-foreground/30">
              <PencilGlyphIcon className="size-6" />
              <p className="text-center font-mono text-[10px] tracking-[0.2em] uppercase text-muted-foreground/40">
                {creatingEntry ? "Creating…" : "Select an entry, or start a new one"}
              </p>
            </div>
          </div>
        )}
      </main>

      {selectedEntry && (
        <DetailsDrawer
          entry={selectedEntry}
          folders={folders}
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          onUpdate={handleDrawerUpdate}
          onDelete={() => setPendingDeleteId(selectedEntry.id)}
          onCoverFileSelected={(file) => void handleCoverFileSelected(file)}
          onFilesSelected={handleFilesSelected}
          onOpenAttachment={(attachment) => void handleOpenAttachment(attachment)}
          onAttachmentError={setError}
          bodyText={bodyText}
          recording={voiceRecorder.recording}
          speechAvailable={voiceRecorder.speechAvailable}
          liveTranscript={voiceRecorder.liveTranscript}
          onToggleRecording={() => {
            if (voiceRecorder.recording) {
              voiceRecorder.stop();
              return;
            }
            if (!selectedEntry) return;
            void voiceRecorder.start();
          }}
        />
      )}

      {pendingDeleteId != null && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-background/60 backdrop-blur-sm"
          onClick={() => setPendingDeleteId(null)}
        >
          <div
            className="rounded-xl border border-foreground/[0.1] bg-card px-6 py-5 max-w-sm w-full mx-4 shadow-xl animate-fade-in"
            onClick={(event) => event.stopPropagation()}
          >
            <p className="text-body text-foreground">Delete this diary entry?</p>
            <p className="mt-1 text-detail text-muted-foreground">This action can't be undone.</p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setPendingDeleteId(null)}
                className="rounded-lg border border-foreground/[0.08] px-3 py-1.5 font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground hover:text-foreground hover:border-foreground/[0.15]"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void confirmDelete()}
                className="rounded-lg border border-destructive/40 px-3 py-1.5 font-mono text-[9px] uppercase tracking-[0.12em] text-destructive hover:bg-destructive/10"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
