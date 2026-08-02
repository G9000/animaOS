import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDownIcon,
  ChevronUpIcon,
  FileIcon,
  ImageIcon,
  MicIcon,
  PlusIcon,
  XIcon,
  cn,
} from "@anima/standard-templates";
import type { DiaryAttachmentData, DiaryEntryData, DiaryFolderData } from "@anima/api-client";
import { EditorContent, useEditor } from "@tiptap/react";
import type { Editor } from "@tiptap/react";
import { marked } from "marked";
import { useAuth } from "../../context/AuthContext";
import { api } from "../../lib/api";
import {
  buildRecordingFilename,
  chooseRecordingMimeType,
  getSpeechRecognitionConstructor,
  type SpeechRecognitionLike,
} from "./lib/speech";
import { createDiaryHtmlSanitizer } from "./lib/sanitize";
import {
  BLANK_BODY_MARKER,
  hasNonTextNode,
  isDiscardablePage,
  isSignificantEdit,
  resolveBodyForSave,
} from "./lib/pageLifecycle";
import { createDiaryExtensions } from "./editor/extensions";
import { DiaryBubbleMenu } from "./editor/BubbleMenu";
import { BlockDragHandle } from "./editor/BlockDragHandle";
import { Glyph } from "./editor/glyphIcons";
import { useAutosave } from "./hooks/useAutosave";
import { PageHeader, useAttachmentBlobUrl } from "./panels/PageHeader";

const MAX_ENTRY_LIMIT = 200;
const ENTRY_PAGE_SIZE = 100;
const MAX_INLINE_IMAGE_BYTES = 3 * 1024 * 1024;

const DIARY_PROSE_CLASS = cn(
  "prose max-w-none",
  "prose-headings:font-semibold prose-headings:tracking-tight",
  "prose-p:leading-relaxed",
  "prose-blockquote:border-l-2 prose-blockquote:border-accent prose-blockquote:not-italic prose-blockquote:text-muted-foreground prose-blockquote:font-normal",
  "prose-code:before:content-none prose-code:after:content-none prose-code:bg-secondary prose-code:rounded-sm prose-code:px-1 prose-code:py-0.5 prose-code:font-normal",
  "prose-pre:bg-secondary prose-pre:border prose-pre:border-border prose-pre:text-foreground",
  "prose-img:rounded-xl prose-img:border prose-img:border-border/60 prose-img:shadow-lg prose-img:max-h-[32rem] prose-img:mx-auto prose-img:block",
  "prose-hr:border-border",
  "prose-li:my-1",
);
const sanitizeDiaryHtml = createDiaryHtmlSanitizer(window);

function todayISODate(): string {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

function formatEntryDate(dateStr: string): string {
  const date = new Date(`${dateStr}T00:00:00`);
  if (Number.isNaN(date.getTime())) return dateStr;
  return date.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: date.getFullYear() !== new Date().getFullYear() ? "numeric" : undefined,
  });
}

function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function attachmentIcon(kind: string) {
  if (kind === "image") return ImageIcon;
  if (kind === "audio") return MicIcon;
  return FileIcon;
}

function PencilGlyphIcon({ className }: { className?: string }) {
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

function FolderGlyphIcon({ className }: { className?: string } = {}) {
  return (
    <Glyph className={className}>
      <path d="M4 6h5l2 2h9v11H4V6Z" />
    </Glyph>
  );
}

function isHtmlBody(body: string): boolean {
  return /^\s*</.test(body);
}

function plainTextOfBody(body: string): string {
  if (!isHtmlBody(body)) return body;
  return body
    .replace(/<\/(p|h[1-6]|li|blockquote|pre)>/g, " ")
    .replace(/<[^>]+>/g, "")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

function escapeHtmlForEditor(text: string): string {
  const escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  const withBreaks = escaped.replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br>");
  return `<p>${withBreaks}</p>`;
}

function entryExcerpt(entry: DiaryEntryData): string {
  const text = plainTextOfBody(entry.body).replace(/\s+/g, " ").trim();
  return text.length > 90 ? `${text.slice(0, 90)}…` : text;
}

function countWords(text: string): number {
  const trimmed = text.trim();
  return trimmed ? trimmed.split(/\s+/).length : 0;
}

const MOOD_PILL_CLASSES = [
  "bg-chart-1/15 text-chart-1 border-chart-1/30",
  "bg-accent-2/15 text-accent-2 border-accent-2/30",
  "bg-chart-4/20 text-chart-4 border-chart-4/40",
  "bg-accent/15 text-accent border-accent/30",
  "bg-chart-3/25 text-chart-3 border-chart-3/40",
];

function moodPillClass(mood: string): string {
  let hash = 0;
  for (let i = 0; i < mood.length; i += 1) {
    hash = (hash * 31 + mood.charCodeAt(i)) >>> 0;
  }
  return MOOD_PILL_CLASSES[hash % MOOD_PILL_CLASSES.length];
}

function isPreviewableAttachment(kind: string): boolean {
  return kind === "image" || kind === "audio" || kind === "video";
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read file."));
    reader.readAsDataURL(file);
  });
}

const MARKDOWN_LINE_PATTERNS = [
  /^#{1,6}\s+\S/, // heading
  /^[-*+]\s+\S/, // bullet list
  /^\d+\.\s+\S/, // ordered list
  /^>\s?\S/, // blockquote
  /^```/, // code fence
  /^-{3,}\s*$/, // horizontal rule
];

const MARKDOWN_INLINE_PATTERNS = [
  /\*\*[^*\n]+\*\*/, // bold
  /`[^`\n]+`/, // inline code
  /\[[^\]]+\]\([^)]+\)/, // link
];

function looksLikeMarkdown(text: string): boolean {
  const lines = text.split("\n");
  if (lines.some((line) => MARKDOWN_LINE_PATTERNS.some((pattern) => pattern.test(line)))) {
    return true;
  }
  return MARKDOWN_INLINE_PATTERNS.some((pattern) => pattern.test(text));
}

// The one place the editor's ProseMirror doc is walked to decide whether it
// carries content that isn't plain text. Delegates the actual "does this
// set of node names count as non-text" decision to pageLifecycle's
// `hasNonTextNode`, which is pure and unit-tested against plain string
// lists — this function is just the Tiptap-specific adapter that collects
// the node names to feed it. See the CAUTION comment on isDiscardablePage
// for why this matters: bodyPlainText alone (e.g. editor.getText()) is ""
// for a page whose only content is an image, an empty table, a divider, or
// an empty callout/details/task item.
function editorHasNonTextContent(ed: Editor): boolean {
  const nodeTypeNames = new Set<string>();
  ed.state.doc.descendants((node) => {
    nodeTypeNames.add(node.type.name);
  });
  return hasNonTextNode(nodeTypeNames);
}

function AttachmentPreview({
  attachment,
  onError,
}: {
  attachment: DiaryAttachmentData;
  onError: (message: string) => void;
}) {
  const previewUrl = useAttachmentBlobUrl(attachment, onError);

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
    <video
      controls
      src={previewUrl}
      className="max-h-64 max-w-full rounded-lg border border-foreground/[0.08]"
    />
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

export default function DiaryWorkspace() {
  const { user } = useAuth();
  const [entries, setEntries] = useState<DiaryEntryData[]>([]);
  const [entryLimit, setEntryLimit] = useState(ENTRY_PAGE_SIZE);
  const [loading, setLoading] = useState(true);
  const [creatingEntry, setCreatingEntry] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [bodyText, setBodyText] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [recording, setRecording] = useState(false);
  const [speechAvailable, setSpeechAvailable] = useState(false);
  const [liveTranscript, setLiveTranscript] = useState("");
  const [deletingEntryId, setDeletingEntryId] = useState<number | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [moodFilter, setMoodFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [folders, setFolders] = useState<DiaryFolderData[]>([]);
  const [activeFolderId, setActiveFolderId] = useState<number | null>(null);
  const [isAddingFolder, setIsAddingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [editingFolderId, setEditingFolderId] = useState<number | null>(null);
  const [editingFolderName, setEditingFolderName] = useState("");
  const [pendingDeleteFolderId, setPendingDeleteFolderId] = useState<number | null>(null);
  const [isDraggingFile, setIsDraggingFile] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const recordedChunksRef = useRef<BlobPart[]>([]);
  const editorWrapperRef = useRef<HTMLDivElement | null>(null);
  const hiddenImageInputRef = useRef<HTMLInputElement | null>(null);

  // Kept fresh every render (not via an effect) so that plain React effect
  // cleanups — which otherwise close over stale values from whenever they
  // were registered — always see the latest entry/title/content snapshot.
  // Mirrors the existing `editorRef.current = editor;` pattern below.
  const selectedEntryRef = useRef<DiaryEntryData | null>(null);
  const titleRef = useRef("");
  // The last body HTML this component itself fed INTO the editor via
  // setContent. Compared against onUpdate's output as a defense-in-depth
  // guard against a save loop, on top of loading content with
  // `{ emitUpdate: false }` (which should already suppress onUpdate for
  // programmatic loads).
  const lastLoadedBodyRef = useRef<string | null>(null);
  // Kept in sync on every edit and every load — see syncEditorContent —
  // so the untitled-page cleanup can read a fresh snapshot without ever
  // touching a possibly-already-destroyed editor instance from an unmount
  // cleanup.
  const lastContentSnapshotRef = useRef<{ bodyPlainText: string; hasNonTextContent: boolean }>({
    bodyPlainText: "",
    hasNonTextContent: false,
  });

  const syncEditorContent = (ed: Editor) => {
    const plainText = ed.getText();
    setBodyText(plainText);
    lastContentSnapshotRef.current = {
      bodyPlainText: plainText,
      hasNonTextContent: editorHasNonTextContent(ed),
    };
  };

  const editor = useEditor({
    extensions: createDiaryExtensions({
      placeholder: "Write your thoughts… ( '/' for commands · drop or paste an image )",
      onImageRequest: () => hiddenImageInputRef.current?.click(),
    }),
    editorProps: {
      attributes: {
        class: cn("tiptap", DIARY_PROSE_CLASS, "min-h-[40vh] text-base leading-loose"),
      },
      handlePaste: (_view, event) => {
        const items = event.clipboardData?.items;
        if (items) {
          const imageFiles = Array.from(items)
            .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
            .map((item) => item.getAsFile())
            .filter((file): file is File => file !== null);
          if (imageFiles.length > 0) {
            event.preventDefault();
            // Matches the pre-Task-11 behavior: pasted image FILES (e.g.
            // copied from Finder) become attachments, same as the "Attach"
            // button — distinct from pasting/dropping into the composer as
            // an inline embed. `editorProps.handlePaste` is captured once
            // at editor creation (unlike onUpdate, it is not kept live by
            // @tiptap/react), so this deliberately reads `selectedEntryRef`
            // and calls `uploadAttachmentFile` rather than closing over
            // `selectedEntry`/`handleFilesSelected` directly — both of
            // those are fresh-per-render closures that would otherwise be
            // frozen at whatever render first created the editor.
            const entryId = selectedEntryRef.current?.id;
            if (entryId != null) {
              for (const file of imageFiles) void uploadAttachmentFile(entryId, file);
            }
            return true;
          }
        }

        // Clipboards from other rich-text apps already carry HTML that
        // ProseMirror parses natively; only reinterpret plain text that
        // looks like raw markdown (e.g. pasted from a .md file or a
        // markdown-speaking chat), so a real paste doesn't lose formatting.
        const html = event.clipboardData?.getData("text/html");
        const text = event.clipboardData?.getData("text/plain");
        if (!html?.trim() && text?.trim() && looksLikeMarkdown(text)) {
          event.preventDefault();
          const parsedHtml = marked.parse(text, { async: false, gfm: true, breaks: false });
          editorRef.current?.chain().focus().insertContent(parsedHtml).run();
          if (editorRef.current) syncEditorContent(editorRef.current);
          return true;
        }
        return false;
      },
    },
    onUpdate: ({ editor: e }) => {
      syncEditorContent(e);

      const html = sanitizeDiaryHtml(e.getHTML());
      const entry = selectedEntryRef.current;
      if (!entry) return;

      // Fix round 1, Finding 1 (CRITICAL): this used to also gate on
      // `canSaveDiaryEntry`, which returns false once the editor is empty
      // with no attachments — so clearing all of an entry's text (a
      // legitimate, intentional edit) never scheduled a save, and the old
      // body silently survived server-side. `isSignificantEdit` is the
      // correct gate here: it only skips the case where the editor's
      // output exactly matches what was just loaded into it (the
      // `setContent(html, { emitUpdate: false })` echo this guards
      // against as a second line of defense against a save loop) — an
      // intentional clear produces different output and is always
      // scheduled, with an explicit blank body (see resolveBodyForSave).
      if (!isSignificantEdit({ loadedHtml: lastLoadedBodyRef.current ?? "", currentHtml: html })) {
        return;
      }
      // Advance the reference point to this edit's output. Without this,
      // lastLoadedBodyRef would stay pinned to whatever was loaded when
      // the entry was opened: type "hello" (schedules + saves), then
      // delete it back to exactly the original pristine content — the
      // comparison above would see current === the *original* load and
      // wrongly call it a no-op, even though the server still has
      // "hello" persisted from the intermediate save and the deletion
      // itself needs to be scheduled. Advancing here means the
      // comparison is always against the last edit actually scheduled,
      // not the entry's initial state.
      lastLoadedBodyRef.current = html;

      const plainText = e.getText();
      const body = resolveBodyForSave({
        editorIsEmpty: e.isEmpty,
        editorHtml: html,
        plainText,
        attachmentCount: entry.attachments.length,
      });
      schedule({ title: titleRef.current, body });
    },
  });
  const editorRef = useRef<Editor | null>(editor);
  editorRef.current = editor;

  const selectedEntry = useMemo(
    () => entries.find((entry) => entry.id === selectedId) ?? null,
    [entries, selectedId],
  );
  selectedEntryRef.current = selectedEntry;

  const wordCount = useMemo(() => countWords(bodyText), [bodyText]);

  const availableMoods = useMemo(() => {
    const moods = new Set<string>();
    for (const entry of entries) {
      if (entry.mood) moods.add(entry.mood);
    }
    return Array.from(moods).sort();
  }, [entries]);

  const filteredEntries = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return entries.filter((entry) => {
      if (activeFolderId != null && entry.folderId !== activeFolderId) return false;
      if (moodFilter && entry.mood !== moodFilter) return false;
      if (dateFrom && entry.entryDate < dateFrom) return false;
      if (dateTo && entry.entryDate > dateTo) return false;
      if (!query) return true;
      const haystack = `${entry.title ?? ""} ${plainTextOfBody(entry.body)} ${entry.mood ?? ""}`.toLowerCase();
      return haystack.includes(query);
    });
  }, [entries, searchQuery, moodFilter, dateFrom, dateTo, activeFolderId]);

  const hasActiveFilters = Boolean(searchQuery || moodFilter || dateFrom || dateTo);

  const loadEntries = async (showLoader = true) => {
    if (user?.id == null) return;
    if (showLoader) {
      setLoading(true);
    }
    try {
      const diaryEntries = await api.diary.list(user.id, entryLimit);
      setEntries(diaryEntries);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load diary.");
    } finally {
      if (showLoader) {
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    void loadEntries();
  }, [user?.id, entryLimit]);

  const loadFolders = async () => {
    if (user?.id == null) return;
    try {
      const list = await api.diary.folders.list(user.id);
      setFolders(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load folders.");
    }
  };

  useEffect(() => {
    void loadFolders();
  }, [user?.id]);

  useEffect(() => {
    setSpeechAvailable(getSpeechRecognitionConstructor() !== null);
    return () => {
      recognitionRef.current?.abort();
      if (mediaRecorderRef.current?.state === "recording") {
        mediaRecorderRef.current.stop();
      }
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

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
      const trimmedTitle = payload.title.trim();
      const updated = await api.diary.update(entryId, {
        body: payload.body,
        title: trimmedTitle || undefined,
        clearTitle: !trimmedTitle,
      });
      setEntries((current) => current.map((e) => (e.id === updated.id ? updated : e)));
    },
  });

  // A page the user created but never touched (per lib/pageLifecycle.ts).
  // Always flushes any pending autosave first so the decision is made
  // against fully up-to-date state, then deletes if still discardable.
  // Never call this against the entry the user is currently editing —
  // only against the one being left (a switch target, or on unmount).
  //
  // Fix round 1, Finding 3: on the true-unmount path this `flush()` call
  // is only a real barrier because useAutosave's own teardown no longer
  // nulls its scheduler ref before flushing (see the comment in
  // useAutosave.ts) — `flush` here reaches the live scheduler for
  // whichever entry is being left regardless of which of the two
  // unmount-time cleanups (this one, or useAutosave's own) happens to run
  // first, rather than silently resolving instantly against a null ref.
  const evaluateAndMaybeDiscard = async (entry: DiaryEntryData) => {
    await flush();
    const snapshot = lastContentSnapshotRef.current;
    const discardable = isDiscardablePage({
      title: titleRef.current,
      bodyPlainText: snapshot.bodyPlainText,
      attachmentCount: entry.attachments.length,
      coverAttachmentId: entry.coverAttachmentId,
      hasNonTextContent: snapshot.hasNonTextContent,
    });
    if (!discardable) return;
    try {
      await api.diary.delete(entry.id);
      setEntries((current) => current.filter((e) => e.id !== entry.id));
    } catch {
      // Best-effort cleanup only — a failed delete just leaves an empty
      // page behind, which is no worse than before this cleanup existed.
    }
  };

  // Load the selected entry's content into the editor. Keyed on the id
  // alone (not the whole `selectedEntry` object) so an unrelated update —
  // e.g. an attachment upload replacing the entry in `entries` — never
  // re-triggers this and clobbers in-progress typing.
  useEffect(() => {
    if (!editor || !selectedEntry) return;
    const html = sanitizeDiaryHtml(
      isHtmlBody(selectedEntry.body) ? selectedEntry.body : escapeHtmlForEditor(selectedEntry.body),
    );
    lastLoadedBodyRef.current = html;
    // emitUpdate: false is the primary guard against a save loop — this
    // programmatic load must never be mistaken for a user edit and
    // scheduled straight back to the server.
    editor.commands.setContent(html, { emitUpdate: false });
    syncEditorContent(editor);
    titleRef.current = selectedEntry.title ?? "";
    window.setTimeout(() => editor.commands.focus("end"), 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedEntry?.id]);

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
      if (editorRef.current) syncEditorContent(editorRef.current);
    } catch {
      setError(`Failed to embed "${file.name}".`);
    }
  };

  const stopAnyActiveRecording = () => {
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
    }
    recognitionRef.current?.abort();
    setRecording(false);
    setLiveTranscript("");
  };

  const startNewEntry = async () => {
    if (user?.id == null || creatingEntry) return;
    const leaving = selectedEntryRef.current;
    stopAnyActiveRecording();
    setCreatingEntry(true);
    setError(null);
    try {
      if (leaving) await evaluateAndMaybeDiscard(leaving);
      const created = await api.diary.create(user.id, {
        entryDate: todayISODate(),
        title: null,
        body: BLANK_BODY_MARKER,
        mood: null,
        folderId: activeFolderId,
      });
      setEntries((current) => [created, ...current]);
      setSelectedId(created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create diary entry.");
    } finally {
      setCreatingEntry(false);
    }
  };

  const selectEntry = async (entryId: number) => {
    if (entryId === selectedId) return;
    const leaving = selectedEntryRef.current;
    stopAnyActiveRecording();
    if (leaving) await evaluateAndMaybeDiscard(leaving);
    setSelectedId(entryId);
  };

  const uploadAttachmentFile = async (entryId: number, file: File) => {
    try {
      const uploaded = await api.diary.uploadAttachment(entryId, file);
      setEntries((current) =>
        current.map((entry) =>
          entry.id === entryId
            ? { ...entry, attachments: [...entry.attachments, uploaded] }
            : entry,
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to attach "${file.name}".`);
    }
  };

  const handleFilesSelected = (selected: FileList | null) => {
    if (!selected || selected.length === 0 || !selectedEntry) return;
    const entryId = selectedEntry.id;
    for (const file of Array.from(selected)) {
      void uploadAttachmentFile(entryId, file);
    }
  };

  const handleInlineImageFilesSelected = (selected: FileList | null) => {
    if (!selected || selected.length === 0) return;
    for (const file of Array.from(selected)) {
      void insertInlineImage(file);
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

  const startSpeechRecognition = () => {
    const SpeechRecognition = getSpeechRecognitionConstructor();
    if (!SpeechRecognition) return;

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = navigator.language || "en-US";
    recognition.onresult = (event) => {
      let finalText = "";
      let interimText = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const transcript = result[0]?.transcript ?? "";
        if (result.isFinal) {
          finalText += transcript;
        } else {
          interimText += transcript;
        }
      }
      if (finalText.trim()) {
        const ed = editorRef.current;
        if (ed) {
          ed.commands.insertContentAt(ed.state.doc.content.size, `${finalText.trim()} `);
          syncEditorContent(ed);
        }
      }
      setLiveTranscript(interimText.trim());
    };
    recognition.onerror = () => {
      setLiveTranscript("");
    };
    recognition.onend = () => {
      setLiveTranscript("");
    };

    try {
      recognition.start();
      recognitionRef.current = recognition;
    } catch {
      recognitionRef.current = null;
    }
  };

  const releaseRecordingResources = () => {
    mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    mediaStreamRef.current = null;
    mediaRecorderRef.current = null;
    recognitionRef.current = null;
  };

  const startRecording = async () => {
    if (recording || !selectedEntry) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("Audio recording is not available in this environment.");
      return;
    }

    setError(null);
    recordedChunksRef.current = [];

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const requestedMimeType = chooseRecordingMimeType();
      const recorder = requestedMimeType
        ? new MediaRecorder(stream, { mimeType: requestedMimeType })
        : new MediaRecorder(stream);

      mediaStreamRef.current = stream;
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          recordedChunksRef.current.push(event.data);
        }
      };
      recorder.onerror = () => {
        setError("Recording failed.");
        setRecording(false);
        releaseRecordingResources();
      };
      recorder.onstop = () => {
        const chunks = recordedChunksRef.current;
        const mimeType = recorder.mimeType || requestedMimeType || "audio/webm";
        const entryId = selectedEntryRef.current?.id ?? null;
        if (chunks.length > 0 && entryId != null) {
          const blob = new Blob(chunks, { type: mimeType });
          const file = new File([blob], buildRecordingFilename(new Date(), mimeType), {
            type: mimeType,
          });
          void uploadAttachmentFile(entryId, file);
        }
        recordedChunksRef.current = [];
        setRecording(false);
        setLiveTranscript("");
        releaseRecordingResources();
      };

      startSpeechRecognition();
      recorder.start(1000);
      setRecording(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start recording.");
      setRecording(false);
      releaseRecordingResources();
    }
  };

  const stopRecording = () => {
    recognitionRef.current?.stop();
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
      return;
    }
    setRecording(false);
    releaseRecordingResources();
  };

  const handleDelete = async (entryId: number) => {
    if (deletingEntryId !== null) return;
    setDeletingEntryId(entryId);
    setError(null);
    try {
      await api.diary.delete(entryId);
      setEntries((current) => current.filter((entry) => entry.id !== entryId));
      if (selectedId === entryId) {
        setSelectedId(null);
      }
      await Promise.all([loadEntries(false), loadFolders()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete diary entry.");
    } finally {
      setDeletingEntryId(null);
    }
  };

  const confirmDelete = async () => {
    if (pendingDeleteId == null) return;
    const entryId = pendingDeleteId;
    setPendingDeleteId(null);
    await handleDelete(entryId);
  };

  const handleOpenAttachment = async (attachment: DiaryAttachmentData) => {
    try {
      const blob = await api.diary.downloadAttachment(attachment.entryId, attachment.id);
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

  const clearFilters = () => {
    setSearchQuery("");
    setMoodFilter("");
    setDateFrom("");
    setDateTo("");
  };

  const setCoverAttachment = async (entryId: number, attachmentId: number) => {
    try {
      const updated = await api.diary.update(entryId, { coverAttachmentId: attachmentId });
      setEntries((current) => current.map((entry) => (entry.id === updated.id ? updated : entry)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to set cover image.");
    }
  };

  const clearCoverAttachment = async (entryId: number) => {
    try {
      const updated = await api.diary.update(entryId, { clearCover: true });
      setEntries((current) => current.map((entry) => (entry.id === updated.id ? updated : entry)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove cover image.");
    }
  };

  const handleCoverFileSelected = async (file: File) => {
    if (!selectedEntry) return;
    try {
      const uploaded = await api.diary.uploadAttachment(selectedEntry.id, file);
      // setCoverAttachment's PATCH response is the server's authoritative
      // entry, which already includes this upload in its attachments array
      // (the upload committed first) — do not also append `uploaded`
      // locally, or it ends up duplicated.
      await setCoverAttachment(selectedEntry.id, uploaded.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to set cover image.");
    }
  };

  const handleDateChange = async (entryDate: string) => {
    if (!selectedEntry || entryDate === selectedEntry.entryDate) return;
    try {
      const updated = await api.diary.update(selectedEntry.id, { entryDate });
      setEntries((current) => current.map((e) => (e.id === updated.id ? updated : e)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update the entry date.");
    }
  };

  const handleMoodChange = async (mood: string) => {
    if (!selectedEntry) return;
    const trimmed = mood.trim();
    if (trimmed === (selectedEntry.mood ?? "")) return;
    try {
      const updated = await api.diary.update(selectedEntry.id, {
        mood: trimmed || undefined,
        clearMood: !trimmed,
      });
      setEntries((current) => current.map((e) => (e.id === updated.id ? updated : e)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update mood.");
    }
  };

  const handleTitleChange = (newTitle: string) => {
    titleRef.current = newTitle;
    const ed = editorRef.current;
    const entry = selectedEntryRef.current;
    if (!ed || !entry) return;
    // No canSaveDiaryEntry-style eligibility gate here (fix round 1,
    // Finding 1): PageHeader's title input only calls onTitleChange from
    // its own onChange, which only fires on a genuine keystroke, so every
    // call here already represents a real edit — including typing a title
    // onto an otherwise-empty entry, which must schedule a save with an
    // explicit blank body rather than being silently dropped.
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

  const createFolder = async () => {
    const name = newFolderName.trim();
    setIsAddingFolder(false);
    setNewFolderName("");
    if (!name || user?.id == null) return;
    try {
      const folder = await api.diary.folders.create(user.id, name);
      setFolders((current) => [...current, folder]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create folder.");
    }
  };

  const startRenameFolder = (folder: DiaryFolderData) => {
    setEditingFolderId(folder.id);
    setEditingFolderName(folder.name);
  };

  const commitRenameFolder = async () => {
    const folderId = editingFolderId;
    const name = editingFolderName.trim();
    setEditingFolderId(null);
    if (folderId == null || !name) return;
    try {
      const updated = await api.diary.folders.rename(folderId, name);
      setFolders((current) => current.map((folder) => (folder.id === folderId ? updated : folder)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to rename folder.");
    }
  };

  const confirmDeleteFolder = async () => {
    if (pendingDeleteFolderId == null) return;
    const folderId = pendingDeleteFolderId;
    setPendingDeleteFolderId(null);
    try {
      await api.diary.folders.delete(folderId);
      setFolders((current) => current.filter((folder) => folder.id !== folderId));
      setEntries((current) =>
        current.map((entry) => (entry.folderId === folderId ? { ...entry, folderId: null } : entry)),
      );
      if (activeFolderId === folderId) setActiveFolderId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete folder.");
    }
  };

  const moveEntryToFolder = async (entryId: number, folderId: number | null) => {
    try {
      const updated = await api.diary.update(entryId, {
        folderId: folderId ?? undefined,
        clearFolder: folderId == null,
      });
      setEntries((current) => current.map((entry) => (entry.id === updated.id ? updated : entry)));
      await loadFolders();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to move entry.");
    }
  };

  return (
    <div className="h-full pt-16 p-4 flex gap-4 overflow-hidden">
      {/* Sidebar — entry list */}
      <aside className="w-80 shrink-0 rounded-xl border border-foreground/[0.08] bg-background/95 backdrop-blur-[36px] shadow-[0_4px_28px_rgba(0,0,0,0.18)] flex flex-col overflow-hidden">
        <div className="flex items-center justify-between gap-3 px-5 py-4">
          <div>
            <h1 className="font-['Playfair_Display'] text-2xl font-semibold text-foreground">
              Diary
            </h1>
            <p className="font-mono text-[9px] tracking-[0.22em] uppercase text-muted-foreground/50 mt-1">
              {entries.length} {entries.length === 1 ? "entry" : "entries"}
            </p>
          </div>
          <button
            type="button"
            onClick={() => void startNewEntry()}
            disabled={creatingEntry}
            title="New entry"
            className="h-9 w-9 flex items-center justify-center rounded-lg bg-accent text-accent-foreground shadow-[0_2px_10px_rgba(0,0,0,0.25)] hover:brightness-110 transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <PlusIcon size="sm" />
          </button>
        </div>

        <div className="border-b border-foreground/[0.08] px-2 py-2 space-y-0.5">
          <button
            type="button"
            onClick={() => setActiveFolderId(null)}
            className={cn(
              "w-full flex items-center gap-2 rounded-lg px-3 py-1.5 text-left transition-colors",
              activeFolderId === null
                ? "bg-accent/15 text-accent"
                : "text-foreground hover:bg-foreground/[0.05]",
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
                  onClick={() => setActiveFolderId(folder.id)}
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
                  <span className="font-mono text-[9px] text-muted-foreground/40">
                    {folder.entryCount}
                  </span>
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
              onBlur={() => void createFolder()}
              onKeyDown={(event) => {
                if (event.key === "Enter") void createFolder();
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
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
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
            </div>
          ) : filteredEntries.length === 0 ? (
            <div className="flex flex-col items-center gap-2 px-4 py-10 text-muted-foreground/30">
              <SearchGlyphIcon />
              <p className="text-center font-mono text-[10px] tracking-[0.2em] uppercase text-muted-foreground/40">
                No matching entries
              </p>
            </div>
          ) : (
            <ul className="space-y-1">
              {filteredEntries.map((entry) => (
                <li key={entry.id}>
                  <button
                    type="button"
                    onClick={() => void selectEntry(entry.id)}
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
                              setPendingDeleteId(entry.id);
                            }}
                            disabled={deletingEntryId === entry.id}
                            title="Delete entry"
                            className="opacity-0 group-hover/entry:opacity-100 inline-flex items-center justify-center h-6 w-6 rounded-full text-muted-foreground/50 hover:text-destructive hover:bg-destructive/10 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
                          >
                            <XIcon size="sm" />
                          </button>
                        </div>
                        {entry.title && (
                          <p className="mt-1 text-body font-medium text-foreground truncate">
                            {entry.title}
                          </p>
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
          {!loading && entries.length >= entryLimit && entryLimit < MAX_ENTRY_LIMIT && (
            <button
              type="button"
              onClick={() => setEntryLimit((limit) => Math.min(MAX_ENTRY_LIMIT, limit + ENTRY_PAGE_SIZE))}
              className="w-full mt-1 py-2.5 rounded-lg font-mono text-[9px] uppercase tracking-[0.18em] text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
            >
              Load more
            </button>
          )}
        </div>
      </aside>

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
                  folders={folders}
                  saveStatus={saveStatus}
                  onRetry={() => void retry()}
                  onTitleChange={handleTitleChange}
                  onToggleDrawer={() => setDrawerOpen((open) => !open)}
                  drawerOpen={drawerOpen}
                  onDateChange={(date) => void handleDateChange(date)}
                  onMoodChange={(mood) => void handleMoodChange(mood)}
                  onFolderChange={(folderId) => void moveEntryToFolder(selectedEntry.id, folderId)}
                  onCoverFileSelected={(file) => void handleCoverFileSelected(file)}
                  onRemoveCover={() => void clearCoverAttachment(selectedEntry.id)}
                  onAttachmentError={setError}
                />

                <div
                  ref={editorWrapperRef}
                  className="diary-editor-shell relative mt-4 flex-1 min-h-[40vh] cursor-text"
                  onClick={() => editor?.commands.focus()}
                  onDragOver={handleComposerDragOver}
                  onDragLeave={handleComposerDragLeave}
                  onDrop={handleComposerDrop}
                >
                  <DiaryBubbleMenu editor={editor} />
                  <BlockDragHandle editor={editor} />
                  <EditorContent editor={editor} />
                  {isDraggingFile && (
                    <div className="absolute inset-0 z-30 flex items-center justify-center rounded-xl border-2 border-dashed border-accent/60 bg-background/80 pointer-events-none">
                      <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-accent">
                        Drop to attach
                      </p>
                    </div>
                  )}
                </div>

                {drawerOpen && (
                  <div className="mt-4 pt-4 border-t border-foreground/[0.08] space-y-4">
                    <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-muted-foreground/50">
                      Attachments
                    </p>
                    {selectedEntry.attachments.length === 0 ? (
                      <p className="text-detail text-muted-foreground/50">No attachments yet.</p>
                    ) : (
                      <>
                        {selectedEntry.attachments.filter(
                          (a) =>
                            isPreviewableAttachment(a.kind) && a.id !== selectedEntry.coverAttachmentId,
                        ).length > 0 && (
                          <div className="flex flex-wrap gap-3">
                            {selectedEntry.attachments
                              .filter(
                                (attachment) =>
                                  isPreviewableAttachment(attachment.kind) &&
                                  attachment.id !== selectedEntry.coverAttachmentId,
                              )
                              .map((attachment) => (
                                <div key={attachment.id} className="relative group space-y-1">
                                  <AttachmentPreview attachment={attachment} onError={setError} />
                                  {attachment.kind === "image" && (
                                    <button
                                      type="button"
                                      onClick={() =>
                                        void setCoverAttachment(selectedEntry.id, attachment.id)
                                      }
                                      className="absolute top-1.5 left-1.5 opacity-0 group-hover:opacity-100 transition-opacity inline-flex items-center gap-1 rounded-lg bg-background/80 border border-foreground/[0.1] px-1.5 py-0.5 font-mono text-[8px] uppercase tracking-[0.12em] text-muted-foreground hover:text-foreground"
                                    >
                                      <StarGlyphIcon className="size-3" />
                                      Set cover
                                    </button>
                                  )}
                                  <button
                                    type="button"
                                    onClick={() => void handleOpenAttachment(attachment)}
                                    className="font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground/60 hover:text-foreground"
                                  >
                                    {attachment.filename || attachment.kind} ·{" "}
                                    {formatFileSize(attachment.sizeBytes)}
                                  </button>
                                </div>
                              ))}
                          </div>
                        )}
                        {selectedEntry.attachments.filter((a) => !isPreviewableAttachment(a.kind))
                          .length > 0 && (
                          <div className="flex flex-wrap gap-1.5">
                            {selectedEntry.attachments
                              .filter((attachment) => !isPreviewableAttachment(attachment.kind))
                              .map((attachment) => {
                                const Icon = attachmentIcon(attachment.kind);
                                return (
                                  <button
                                    key={attachment.id}
                                    type="button"
                                    onClick={() => void handleOpenAttachment(attachment)}
                                    className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-foreground/[0.08] bg-foreground/[0.03] px-2 py-1 text-caption text-muted-foreground hover:text-foreground hover:border-foreground/[0.15]"
                                  >
                                    <Icon size="sm" className="shrink-0" />
                                    <span className="truncate">
                                      {attachment.filename || attachment.kind}
                                    </span>
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
                  </div>
                )}
              </div>
            </div>

            {/* Bottom toolbar */}
            <div className="border-t border-foreground/[0.08]">
              <div className="max-w-3xl mx-auto px-8 py-3 space-y-2">
                {recording && (
                  <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-2.5 py-1.5 font-mono text-[9px] uppercase tracking-[0.16em] text-destructive">
                    <span>{speechAvailable ? "Recording / transcribing" : "Recording"}</span>
                    {liveTranscript && (
                      <span className="ml-2 normal-case tracking-normal text-foreground/70">
                        {liveTranscript}
                      </span>
                    )}
                  </div>
                )}

                <div className="flex items-center justify-between gap-2">
                  <div className="flex flex-wrap gap-2">
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
                    <button
                      type="button"
                      onClick={() => void (recording ? stopRecording() : startRecording())}
                      className={cn(
                        "inline-flex items-center rounded-lg border px-3 py-2 text-[9px] uppercase tracking-[0.12em] font-mono transition-colors",
                        recording
                          ? "border-destructive/40 text-destructive bg-destructive/10"
                          : "border-foreground/[0.08] bg-foreground/[0.03] text-muted-foreground hover:text-foreground hover:border-foreground/[0.15]",
                      )}
                    >
                      <MicIcon size="sm" className="mr-2" />
                      {recording ? "Stop" : "Record"}
                    </button>
                  </div>
                  {wordCount > 0 && (
                    <span className="font-mono text-[9px] tracking-[0.16em] uppercase text-muted-foreground/40">
                      {wordCount} {wordCount === 1 ? "word" : "words"}
                    </span>
                  )}
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
    </div>
  );
}
