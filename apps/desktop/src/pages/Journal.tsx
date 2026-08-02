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
import StarterKit from "@tiptap/starter-kit";
import TiptapImage from "@tiptap/extension-image";
import { marked } from "marked";
import { Placeholder } from "@tiptap/extensions";
import { useAuth } from "../context/AuthContext";
import { api } from "../lib/api";
import {
  buildRecordingFilename,
  chooseRecordingMimeType,
  getSpeechRecognitionConstructor,
  type SpeechRecognitionLike,
} from "../features/diary/lib/speech";
import { canSaveDiaryEntry, resolveDiaryBody } from "../features/diary/lib/snapshot";
import { createDiaryHtmlSanitizer } from "../features/diary/lib/sanitize";

const MAX_ENTRY_LIMIT = 200;
const ENTRY_PAGE_SIZE = 100;
const MAX_INLINE_IMAGE_BYTES = 3 * 1024 * 1024;

const INLINE_IMAGE_CLASS =
  "rounded-xl border border-border/60 shadow-lg max-h-[32rem] w-auto max-w-full mx-auto block object-contain";

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

function formatEntryDateLong(dateStr: string): string {
  const date = new Date(`${dateStr}T00:00:00`);
  if (Number.isNaN(date.getTime())) return dateStr;
  return date.toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
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

function Glyph({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="square"
      strokeLinejoin="miter"
      className={cn("size-4 shrink-0", className)}
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

function ParagraphGlyphIcon() {
  return (
    <Glyph>
      <path d="M4 6h16M4 12h16M4 18h10" />
    </Glyph>
  );
}

function HeadingGlyphIcon({ level }: { level: 1 | 2 | 3 }) {
  return (
    <Glyph>
      <path d="M5 5v14M13 5v14M5 12h8" />
      <text x="15" y="18" fontSize="8.5" fontFamily="ui-monospace, monospace" fill="currentColor" stroke="none">
        {level}
      </text>
    </Glyph>
  );
}

function BulletListGlyphIcon() {
  return (
    <Glyph>
      <circle cx="5" cy="6" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="5" cy="12" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="5" cy="18" r="1.1" fill="currentColor" stroke="none" />
      <path d="M9.5 6h10M9.5 12h10M9.5 18h10" />
    </Glyph>
  );
}

function OrderedListGlyphIcon() {
  return (
    <Glyph>
      <text x="2" y="8" fontSize="7" fontFamily="ui-monospace, monospace" fill="currentColor" stroke="none">
        1
      </text>
      <text x="2" y="14.5" fontSize="7" fontFamily="ui-monospace, monospace" fill="currentColor" stroke="none">
        2
      </text>
      <text x="2" y="21" fontSize="7" fontFamily="ui-monospace, monospace" fill="currentColor" stroke="none">
        3
      </text>
      <path d="M9.5 6h10M9.5 12.5h10M9.5 19h10" />
    </Glyph>
  );
}

function QuoteGlyphIcon() {
  return (
    <Glyph>
      <path d="M6 8.5c-1.4 0-2.5 1.2-2.5 3.5S4.6 15.5 6 15.5M14 8.5c-1.4 0-2.5 1.2-2.5 3.5s1.1 3.5 2.5 3.5" />
    </Glyph>
  );
}

function CodeGlyphIcon() {
  return (
    <Glyph>
      <path d="M9 7L4 12l5 5M15 7l5 5-5 5" />
    </Glyph>
  );
}

function DividerGlyphIcon() {
  return (
    <Glyph>
      <path d="M5 12h14" />
    </Glyph>
  );
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

function KebabGlyphIcon({ className }: { className?: string } = {}) {
  return (
    <Glyph className={className}>
      <circle cx="12" cy="5" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="12" cy="19" r="1.4" fill="currentColor" stroke="none" />
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

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read file."));
    reader.readAsDataURL(file);
  });
}

function isPreviewableAttachment(kind: string): boolean {
  return kind === "image" || kind === "audio" || kind === "video";
}

interface SlashRange {
  from: number;
  to: number;
}

interface SlashMenuState extends SlashRange {
  query: string;
  top: number;
  left: number;
}

interface SlashCommandItem {
  id: string;
  label: string;
  hint: string;
  icon: React.ReactNode;
  run: (editor: Editor, range: SlashRange) => void;
}

const SLASH_COMMANDS: SlashCommandItem[] = [
  {
    id: "paragraph",
    label: "Text",
    hint: "",
    icon: <ParagraphGlyphIcon />,
    run: (editor, range) => editor.chain().focus().deleteRange(range).setParagraph().run(),
  },
  {
    id: "h1",
    label: "Heading 1",
    hint: "#",
    icon: <HeadingGlyphIcon level={1} />,
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range).setNode("heading", { level: 1 }).run(),
  },
  {
    id: "h2",
    label: "Heading 2",
    hint: "##",
    icon: <HeadingGlyphIcon level={2} />,
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range).setNode("heading", { level: 2 }).run(),
  },
  {
    id: "h3",
    label: "Heading 3",
    hint: "###",
    icon: <HeadingGlyphIcon level={3} />,
    run: (editor, range) =>
      editor.chain().focus().deleteRange(range).setNode("heading", { level: 3 }).run(),
  },
  {
    id: "bullet",
    label: "Bullet list",
    hint: "-",
    icon: <BulletListGlyphIcon />,
    run: (editor, range) => editor.chain().focus().deleteRange(range).toggleBulletList().run(),
  },
  {
    id: "ordered",
    label: "Numbered list",
    hint: "1.",
    icon: <OrderedListGlyphIcon />,
    run: (editor, range) => editor.chain().focus().deleteRange(range).toggleOrderedList().run(),
  },
  {
    id: "quote",
    label: "Quote",
    hint: ">",
    icon: <QuoteGlyphIcon />,
    run: (editor, range) => editor.chain().focus().deleteRange(range).toggleBlockquote().run(),
  },
  {
    id: "code",
    label: "Code block",
    hint: "```",
    icon: <CodeGlyphIcon />,
    run: (editor, range) => editor.chain().focus().deleteRange(range).toggleCodeBlock().run(),
  },
  {
    id: "divider",
    label: "Divider",
    hint: "---",
    icon: <DividerGlyphIcon />,
    run: (editor, range) => editor.chain().focus().deleteRange(range).setHorizontalRule().run(),
  },
  {
    id: "image",
    label: "Image",
    hint: "",
    icon: <ImageIcon size="sm" />,
    // Special-cased by the caller: opens the file picker instead of running
    // an editor command directly, since inserting requires an async file read.
    run: (editor, range) => editor.chain().focus().deleteRange(range).run(),
  },
];

const SLASH_TRIGGER_RE = /(?:^|\s)\/([a-zA-Z0-9]*)$/;

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

function useFileObjectUrl(file: File | null): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!file) {
      setUrl(null);
      return;
    }
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);

  return url;
}

function useAttachmentBlobUrl(
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

function CoverBanner({
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

function PendingFilePreview({ file }: { file: File }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!file.type.startsWith("image/") && !file.type.startsWith("audio/")) return;
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);

  if (!url) return null;
  if (file.type.startsWith("image/")) {
    return (
      <img
        src={url}
        alt={file.name}
        className="h-10 w-10 shrink-0 rounded-md object-cover border border-foreground/[0.08]"
      />
    );
  }
  return <audio controls src={url} className="h-8 max-w-[180px]" />;
}

export default function Journal() {
  const { user } = useAuth();
  const [entries, setEntries] = useState<DiaryEntryData[]>([]);
  const [entryLimit, setEntryLimit] = useState(ENTRY_PAGE_SIZE);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [isEditingSelected, setIsEditingSelected] = useState(false);
  const [entryDate, setEntryDate] = useState(todayISODate);
  const [title, setTitle] = useState("");
  const [bodyText, setBodyText] = useState("");
  const [editorHasContent, setEditorHasContent] = useState(false);
  const [mood, setMood] = useState("");
  const [entryFolderId, setEntryFolderId] = useState<number | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [pendingCoverFile, setPendingCoverFile] = useState<File | null>(null);
  const coverFileInputRef = useRef<HTMLInputElement | null>(null);
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
  const [entryMenuOpen, setEntryMenuOpen] = useState(false);
  const [entryMenuFolderOpen, setEntryMenuFolderOpen] = useState(false);
  const entryMenuRef = useRef<HTMLDivElement | null>(null);
  const [slashMenu, setSlashMenu] = useState<SlashMenuState | null>(null);
  const [slashActiveIndex, setSlashActiveIndex] = useState(0);
  const [isDraggingFile, setIsDraggingFile] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const recordedChunksRef = useRef<BlobPart[]>([]);
  const editorWrapperRef = useRef<HTMLDivElement | null>(null);
  const hiddenImageInputRef = useRef<HTMLInputElement | null>(null);

  const syncSlashMenu = (ed: Editor) => {
    const wrapperEl = editorWrapperRef.current;
    if (!wrapperEl) {
      setSlashMenu(null);
      return;
    }
    const { from } = ed.state.selection;
    const textBefore = ed.state.doc.textBetween(Math.max(0, from - 40), from, "\n", "\n");
    const match = SLASH_TRIGGER_RE.exec(textBefore);
    if (!match) {
      setSlashMenu(null);
      return;
    }
    const query = match[1];
    const slashStart = from - query.length - 1;
    const coords = ed.view.coordsAtPos(from);
    const wrapperRect = wrapperEl.getBoundingClientRect();
    setSlashActiveIndex(0);
    setSlashMenu({
      from: slashStart,
      to: from,
      query,
      top: coords.bottom - wrapperRect.top + 4,
      left: Math.max(0, coords.left - wrapperRect.left),
    });
  };

  const syncEditorContent = (ed: Editor) => {
    setBodyText(ed.getText());
    setEditorHasContent(!ed.isEmpty);
  };

  const editor = useEditor({
    extensions: [
      StarterKit,
      TiptapImage.configure({
        allowBase64: true,
        HTMLAttributes: { class: INLINE_IMAGE_CLASS },
      }),
      Placeholder.configure({
        placeholder: "Write your thoughts… ( '/' for commands · drop or paste an image )",
      }),
    ],
    editorProps: {
      attributes: {
        class: cn("tiptap", DIARY_PROSE_CLASS, "min-h-[40vh] text-base leading-loose"),
      },
      handleKeyDown: (_view, event) => {
        const menu = slashMenuRef.current;
        if (!menu) return false;
        const commands = slashCommandsRef.current;
        if (event.key === "ArrowDown") {
          setSlashActiveIndex((index) => (commands.length ? (index + 1) % commands.length : 0));
          return true;
        }
        if (event.key === "ArrowUp") {
          setSlashActiveIndex((index) =>
            commands.length ? (index - 1 + commands.length) % commands.length : 0,
          );
          return true;
        }
        if (event.key === "Enter" || event.key === "Tab") {
          const command = commands[slashActiveIndexRef.current];
          if (command && editorRef.current) {
            if (command.id === "image") {
              editorRef.current.chain().focus().deleteRange(menu).run();
              hiddenImageInputRef.current?.click();
            } else {
              command.run(editorRef.current, menu);
            }
          }
          setSlashMenu(null);
          return true;
        }
        if (event.key === "Escape") {
          setSlashMenu(null);
          return true;
        }
        return false;
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
            setFiles((current) => [...current, ...imageFiles]);
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
      syncSlashMenu(e);
    },
    onSelectionUpdate: ({ editor: e }) => {
      syncSlashMenu(e);
    },
    onBlur: () => {
      setSlashMenu(null);
    },
  });
  const editorRef = useRef<Editor | null>(editor);
  editorRef.current = editor;

  const filteredSlashCommands = useMemo(() => {
    if (!slashMenu) return [];
    const query = slashMenu.query.toLowerCase();
    if (!query) return SLASH_COMMANDS;
    return SLASH_COMMANDS.filter((command) => command.label.toLowerCase().includes(query));
  }, [slashMenu]);

  const slashMenuRef = useRef<SlashMenuState | null>(slashMenu);
  slashMenuRef.current = slashMenu;
  const slashCommandsRef = useRef<SlashCommandItem[]>(filteredSlashCommands);
  slashCommandsRef.current = filteredSlashCommands;
  const slashActiveIndexRef = useRef(slashActiveIndex);
  slashActiveIndexRef.current = Math.min(
    slashActiveIndex,
    Math.max(filteredSlashCommands.length - 1, 0),
  );

  const runSlashCommand = (command: SlashCommandItem) => {
    const ed = editorRef.current;
    const menu = slashMenuRef.current;
    if (!ed || !menu) return;
    if (command.id === "image") {
      ed.chain().focus().deleteRange(menu).run();
      setSlashMenu(null);
      hiddenImageInputRef.current?.click();
      return;
    }
    command.run(ed, menu);
    setSlashMenu(null);
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
      if (editorRef.current) syncEditorContent(editorRef.current);
    } catch {
      setError(`Failed to embed "${file.name}".`);
    }
  };

  const selectedEntry = useMemo(
    () => entries.find((entry) => entry.id === selectedId) ?? null,
    [entries, selectedId],
  );
  const coverAttachment = useMemo(() => {
    if (!selectedEntry?.coverAttachmentId) return null;
    return (
      selectedEntry.attachments.find((a) => a.id === selectedEntry.coverAttachmentId) ?? null
    );
  }, [selectedEntry]);
  const pendingCoverPreviewUrl = useFileObjectUrl(pendingCoverFile);
  const composerCoverAttachment = isEditingSelected ? coverAttachment : null;
  const wordCount = useMemo(() => countWords(bodyText), [bodyText]);
  const canSave = canSaveDiaryEntry({
    editorHasContent,
    plainText: bodyText,
    attachmentCount: files.length,
    hasPendingCover: pendingCoverFile !== null,
  });

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
    setEntryMenuOpen(false);
    setEntryMenuFolderOpen(false);
  }, [selectedId]);

  useEffect(() => {
    if (!entryMenuOpen) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (entryMenuRef.current && !entryMenuRef.current.contains(event.target as Node)) {
        setEntryMenuOpen(false);
        setEntryMenuFolderOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [entryMenuOpen]);

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

  const releaseRecordingResources = () => {
    mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    mediaStreamRef.current = null;
    mediaRecorderRef.current = null;
    recognitionRef.current = null;
  };

  const resetComposer = () => {
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
    }
    recognitionRef.current?.abort();
    setRecording(false);
    setLiveTranscript("");
    setTitle("");
    setBodyText("");
    setEditorHasContent(false);
    editorRef.current?.commands.clearContent();
    setMood("");
    setEntryFolderId(null);
    setFiles([]);
    setPendingCoverFile(null);
    setEntryDate(todayISODate());
    setSlashMenu(null);
  };

  const startNewEntry = () => {
    setSelectedId(null);
    setIsEditingSelected(false);
    resetComposer();
    setEntryFolderId(activeFolderId);
    window.setTimeout(() => editorRef.current?.commands.focus("end"), 0);
  };

  const selectEntry = (entryId: number) => {
    if (entryId === selectedId && !isEditingSelected) return;
    setIsEditingSelected(false);
    resetComposer();
    setSelectedId(entryId);
  };

  const startEditEntry = () => {
    if (!selectedEntry) return;
    setTitle(selectedEntry.title ?? "");
    setMood(selectedEntry.mood ?? "");
    setEntryDate(selectedEntry.entryDate);
    const html =
      (isHtmlBody(selectedEntry.body)
        ? selectedEntry.body
        : escapeHtmlForEditor(selectedEntry.body));
    editorRef.current?.commands.setContent(html);
    if (editorRef.current) syncEditorContent(editorRef.current);
    setFiles([]);
    setPendingCoverFile(null);
    setEntryFolderId(selectedEntry.folderId);
    setIsEditingSelected(true);
    window.setTimeout(() => editorRef.current?.commands.focus("end"), 0);
  };

  const cancelEdit = () => {
    setIsEditingSelected(false);
    resetComposer();
  };

  const handleFilesSelected = (selected: FileList | null) => {
    if (!selected || selected.length === 0) return;
    setFiles((current) => [...current, ...Array.from(selected)]);
  };

  const handleInlineImageFilesSelected = (selected: FileList | null) => {
    if (!selected || selected.length === 0) return;
    for (const file of Array.from(selected)) {
      void insertInlineImage(file);
    }
  };

  const removeSelectedFile = (index: number) => {
    setFiles((current) => current.filter((_, i) => i !== index));
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

  const startRecording = async () => {
    if (recording) return;
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
        if (chunks.length > 0) {
          const blob = new Blob(chunks, { type: mimeType });
          const file = new File([blob], buildRecordingFilename(new Date(), mimeType), {
            type: mimeType,
          });
          setFiles((current) => [...current, file]);
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

  const handleSave = async () => {
    if (user?.id == null || !canSave || saving) return;
    if (recording) {
      stopRecording();
      return;
    }
    setSaving(true);
    setError(null);
    const editingEntry = isEditingSelected ? selectedEntry : null;
    try {
      const currentEditor = editorRef.current;
      const bodyValue =
        resolveDiaryBody({
          editorIsEmpty: currentEditor?.isEmpty ?? bodyText.trim().length === 0,
          editorHtml: currentEditor?.getHTML() ?? "",
          plainText: bodyText,
        }) ?? "Attachment-only diary entry.";

      let savedId: number;
      if (editingEntry) {
        const updated = await api.diary.update(editingEntry.id, {
          entryDate,
          body: bodyValue,
          title: title.trim() || undefined,
          clearTitle: !title.trim(),
          mood: mood.trim() || undefined,
          clearMood: !mood.trim(),
          folderId: entryFolderId ?? undefined,
          clearFolder: entryFolderId == null,
        });
        savedId = updated.id;
      } else {
        const created = await api.diary.create(user.id, {
          entryDate,
          title: title.trim() || null,
          body: bodyValue,
          mood: mood.trim() || null,
          folderId: entryFolderId,
        });
        savedId = created.id;
      }

      for (const file of files) {
        await api.diary.uploadAttachment(savedId, file);
      }

      if (pendingCoverFile) {
        const coverUpload = await api.diary.uploadAttachment(savedId, pendingCoverFile);
        await api.diary.update(savedId, { coverAttachmentId: coverUpload.id });
      }

      setIsEditingSelected(false);
      resetComposer();
      await Promise.all([loadEntries(), loadFolders()]);
      setSelectedId(savedId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save diary entry.");
    } finally {
      setSaving(false);
    }
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
        setIsEditingSelected(false);
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
            onClick={startNewEntry}
            title="New entry"
            className="h-9 w-9 flex items-center justify-center rounded-lg bg-accent text-accent-foreground shadow-[0_2px_10px_rgba(0,0,0,0.25)] hover:brightness-110 transition-all active:scale-95"
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
                    onClick={() => selectEntry(entry.id)}
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

      {/* Canvas — write or read */}
      <main className="flex-1 min-w-0 rounded-xl border border-foreground/[0.08] bg-background/95 backdrop-blur-[36px] shadow-[0_4px_28px_rgba(0,0,0,0.18)] flex flex-col overflow-hidden">
        {error && (
          <div className="mx-8 mt-4 border border-destructive/30 bg-destructive/10 px-3 py-2 text-detail text-destructive animate-fade-in">
            {error}
          </div>
        )}

        {selectedEntry && !isEditingSelected ? (
          /* Read mode */
          <div className="flex-1 overflow-y-auto">
            <div key={selectedEntry.id} className="max-w-3xl mx-auto animate-fade-in">
              {coverAttachment && (
                <div className="relative group px-8 pt-8">
                  <CoverBanner attachment={coverAttachment} onError={setError} />
                  <button
                    type="button"
                    onClick={() => void clearCoverAttachment(selectedEntry.id)}
                    className="absolute top-11 right-11 opacity-0 group-hover:opacity-100 transition-opacity inline-flex items-center gap-1.5 rounded-lg bg-background/80 border border-foreground/[0.1] px-2 py-1 font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground hover:text-destructive"
                  >
                    <XIcon size="sm" />
                    Remove cover
                  </button>
                </div>
              )}
              <div className="px-8 py-10">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="font-mono text-[10px] tracking-[0.22em] uppercase text-muted-foreground/80">
                    {formatEntryDateLong(selectedEntry.entryDate)}
                  </p>
                  {selectedEntry.mood && (
                    <span
                      className={cn(
                        "mt-2 inline-block rounded-full border px-2.5 py-0.5 text-[9px] uppercase tracking-[0.16em]",
                        moodPillClass(selectedEntry.mood),
                      )}
                    >
                      {selectedEntry.mood}
                    </span>
                  )}
                </div>
                <div className="relative shrink-0" ref={entryMenuRef}>
                  <button
                    type="button"
                    onClick={() => setEntryMenuOpen((open) => !open)}
                    title="Entry actions"
                    className={cn(
                      "text-muted-foreground/50 hover:text-foreground",
                      entryMenuOpen && "text-foreground",
                    )}
                  >
                    <KebabGlyphIcon />
                  </button>
                  {entryMenuOpen && (
                    <div className="absolute right-0 top-full mt-1 z-40 w-48 rounded-lg border border-foreground/[0.1] bg-card shadow-xl animate-fade-in overflow-hidden">
                      <button
                        type="button"
                        onClick={() => {
                          setEntryMenuOpen(false);
                          startEditEntry();
                        }}
                        className="w-full flex items-center gap-2 px-3 py-2 text-left text-detail text-muted-foreground hover:bg-secondary hover:text-foreground"
                      >
                        <PencilGlyphIcon className="size-3.5" />
                        Edit
                      </button>
                      {folders.length > 0 && (
                        <div className="border-t border-border">
                          <button
                            type="button"
                            onClick={() => setEntryMenuFolderOpen((open) => !open)}
                            className="w-full flex items-center gap-2 px-3 py-2 text-left text-detail text-muted-foreground hover:bg-secondary hover:text-foreground"
                          >
                            <FolderGlyphIcon className="size-3.5" />
                            <span className="flex-1 truncate">
                              {selectedEntry.folderId != null
                                ? (folders.find((f) => f.id === selectedEntry.folderId)?.name ??
                                  "Move to folder")
                                : "Move to folder"}
                            </span>
                            {entryMenuFolderOpen ? (
                              <ChevronUpIcon size="sm" />
                            ) : (
                              <ChevronDownIcon size="sm" />
                            )}
                          </button>
                          {entryMenuFolderOpen && (
                            <div className="bg-secondary/30 max-h-40 overflow-y-auto animate-fade-in">
                              <button
                                type="button"
                                onClick={() => {
                                  setEntryMenuOpen(false);
                                  void moveEntryToFolder(selectedEntry.id, null);
                                }}
                                className={cn(
                                  "w-full px-3 py-1.5 pl-9 text-left text-detail hover:bg-secondary",
                                  selectedEntry.folderId == null
                                    ? "text-foreground"
                                    : "text-muted-foreground",
                                )}
                              >
                                No folder
                              </button>
                              {folders.map((folder) => (
                                <button
                                  key={folder.id}
                                  type="button"
                                  onClick={() => {
                                    setEntryMenuOpen(false);
                                    void moveEntryToFolder(selectedEntry.id, folder.id);
                                  }}
                                  className={cn(
                                    "w-full px-3 py-1.5 pl-9 text-left text-detail hover:bg-secondary truncate",
                                    selectedEntry.folderId === folder.id
                                      ? "text-foreground"
                                      : "text-muted-foreground",
                                  )}
                                >
                                  {folder.name}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                      <button
                        type="button"
                        onClick={() => {
                          setEntryMenuOpen(false);
                          setPendingDeleteId(selectedEntry.id);
                        }}
                        disabled={deletingEntryId === selectedEntry.id}
                        className="w-full flex items-center gap-2 px-3 py-2 text-left text-detail text-destructive border-t border-border hover:bg-destructive/10 disabled:opacity-50"
                      >
                        <XIcon size="sm" />
                        Delete
                      </button>
                    </div>
                  )}
                </div>
              </div>

              {selectedEntry.title && (
                <h2 className="mt-4 font-['Playfair_Display'] text-3xl md:text-4xl font-semibold tracking-tight text-foreground">
                  {selectedEntry.title}
                </h2>
              )}

              {isHtmlBody(selectedEntry.body) ? (
                <div
                  className={cn(DIARY_PROSE_CLASS, "mt-5 text-base leading-loose")}
                  dangerouslySetInnerHTML={{ __html: sanitizeDiaryHtml(selectedEntry.body) }}
                />
              ) : (
                <p className="mt-5 whitespace-pre-wrap text-base leading-loose text-foreground">
                  {selectedEntry.body}
                </p>
              )}

              {selectedEntry.attachments.length > 0 && (
                <div className="mt-8 space-y-4">
                  {selectedEntry.attachments.filter(
                    (a) => isPreviewableAttachment(a.kind) && a.id !== selectedEntry.coverAttachmentId,
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
                                onClick={() => void setCoverAttachment(selectedEntry.id, attachment.id)}
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
                  {selectedEntry.attachments.filter((a) => !isPreviewableAttachment(a.kind)).length >
                    0 && (
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
                </div>
              )}
              </div>
            </div>
          </div>
        ) : (
          /* Write mode (new entry or editing selected entry) */
          <div className="flex-1 min-h-0 flex flex-col">
            <div className="flex-1 min-h-0 overflow-y-auto">
              <div className="max-w-3xl mx-auto px-8 pt-10 pb-4 h-full flex flex-col">
                <div className="flex flex-wrap items-center gap-2">
                  <label className="inline-flex items-center gap-1.5 rounded-full bg-foreground/[0.06] border border-foreground/[0.1] px-2.5 py-1 font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground/90 hover:text-foreground hover:border-foreground/[0.15] transition-colors">
                    <CalendarGlyphIcon className="size-3.5" />
                    <input
                      type="date"
                      value={entryDate}
                      onChange={(event) => setEntryDate(event.target.value)}
                      className="bg-transparent outline-none cursor-pointer"
                    />
                  </label>
                  <input
                    type="text"
                    value={mood}
                    onChange={(event) => setMood(event.target.value)}
                    placeholder="Mood"
                    maxLength={80}
                    className="rounded-full bg-foreground/[0.06] border border-foreground/[0.1] px-2.5 py-1 font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground/90 placeholder:text-muted-foreground/40 outline-none w-28 hover:border-foreground/[0.15] focus:border-accent/50 transition-colors"
                  />
                  {folders.length > 0 && (
                    <label className="inline-flex items-center gap-1.5 rounded-full bg-foreground/[0.06] border border-foreground/[0.1] px-2.5 py-1 font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground/90 hover:text-foreground hover:border-foreground/[0.15] transition-colors">
                      <FolderGlyphIcon className="size-3.5" />
                      <select
                        value={entryFolderId ?? ""}
                        onChange={(event) =>
                          setEntryFolderId(event.target.value ? Number(event.target.value) : null)
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
                  {isEditingSelected && (
                    <button
                      type="button"
                      onClick={cancelEdit}
                      className="font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground hover:text-destructive"
                    >
                      Cancel
                    </button>
                  )}
                  {wordCount > 0 && (
                    <span className="ml-auto font-mono text-[9px] tracking-[0.16em] uppercase text-muted-foreground/40">
                      {wordCount} {wordCount === 1 ? "word" : "words"}
                    </span>
                  )}
                </div>

                <input
                  type="text"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="Untitled entry"
                  maxLength={200}
                  className="mt-6 w-full bg-transparent font-['Playfair_Display'] text-3xl md:text-4xl font-semibold tracking-tight text-foreground placeholder:text-muted-foreground/25 outline-none"
                />

                <div className="mt-4">
                  {pendingCoverFile && pendingCoverPreviewUrl ? (
                    <div className="relative group">
                      <img
                        src={pendingCoverPreviewUrl}
                        alt=""
                        className="w-full h-48 rounded-xl border border-foreground/[0.08] object-cover"
                      />
                      <button
                        type="button"
                        onClick={() => setPendingCoverFile(null)}
                        className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity inline-flex items-center gap-1.5 rounded-lg bg-background/80 border border-foreground/[0.1] px-2 py-1 font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground hover:text-destructive"
                      >
                        <XIcon size="sm" />
                        Remove
                      </button>
                    </div>
                  ) : composerCoverAttachment ? (
                    <div className="relative group">
                      <CoverBanner attachment={composerCoverAttachment} onError={setError} />
                      <button
                        type="button"
                        onClick={() => selectedEntry && void clearCoverAttachment(selectedEntry.id)}
                        className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity inline-flex items-center gap-1.5 rounded-lg bg-background/80 border border-foreground/[0.1] px-2 py-1 font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground hover:text-destructive"
                      >
                        <XIcon size="sm" />
                        Remove cover
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => coverFileInputRef.current?.click()}
                      className="w-full h-12 rounded-xl border border-dashed border-foreground/[0.12] flex items-center justify-center gap-2 text-muted-foreground/60 hover:text-foreground hover:border-foreground/25 transition-colors"
                    >
                      <ImageIcon size="sm" />
                      <span className="font-mono text-[10px] uppercase tracking-[0.14em]">
                        Add cover image
                      </span>
                    </button>
                  )}
                  <input
                    ref={coverFileInputRef}
                    type="file"
                    accept="image/*"
                    className="hidden"
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      if (file) setPendingCoverFile(file);
                      event.target.value = "";
                    }}
                  />
                </div>

                <div
                  ref={editorWrapperRef}
                  className="relative mt-4 flex-1 min-h-[40vh] cursor-text"
                  onClick={() => editor?.commands.focus()}
                  onDragOver={handleComposerDragOver}
                  onDragLeave={handleComposerDragLeave}
                  onDrop={handleComposerDrop}
                >
                  <EditorContent editor={editor} />
                  {isDraggingFile && (
                    <div className="absolute inset-0 z-30 flex items-center justify-center rounded-xl border-2 border-dashed border-accent/60 bg-background/80 pointer-events-none">
                      <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-accent">
                        Drop to attach
                      </p>
                    </div>
                  )}
                  {slashMenu && (
                    <div
                      className="absolute z-40 w-56 rounded-lg border border-foreground/[0.1] bg-card shadow-xl animate-fade-in overflow-hidden"
                      style={{ top: slashMenu.top, left: slashMenu.left }}
                    >
                      {filteredSlashCommands.length === 0 ? (
                        <p className="px-3 py-2 font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground/50">
                          No matching commands
                        </p>
                      ) : (
                        <ul className="py-1">
                          {filteredSlashCommands.map((command, index) => (
                            <li key={command.id}>
                              <button
                                type="button"
                                onMouseDown={(event) => {
                                  event.preventDefault();
                                  runSlashCommand(command);
                                }}
                                onMouseEnter={() => setSlashActiveIndex(index)}
                                className={cn(
                                  "w-full flex items-center gap-2.5 px-3 py-1.5 text-left text-detail transition-colors",
                                  index === slashActiveIndexRef.current
                                    ? "bg-secondary text-foreground"
                                    : "text-muted-foreground hover:bg-secondary/50",
                                )}
                              >
                                {command.icon}
                                <span className="flex-1 truncate">{command.label}</span>
                                <span className="font-mono text-[9px] text-muted-foreground/40">
                                  {command.hint}
                                </span>
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                </div>

                {isEditingSelected && selectedEntry && selectedEntry.attachments.length > 0 && (
                  <div className="mt-4 pt-4 border-t border-foreground/[0.08]">
                    <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-muted-foreground/50 mb-2">
                      Existing attachments
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {selectedEntry.attachments.map((attachment) => {
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
                          </button>
                        );
                      })}
                    </div>
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

                {files.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {files.map((file, index) => {
                      const Icon = attachmentIcon(file.type.split("/", 1)[0]);
                      return (
                        <span
                          key={`${file.name}-${index}`}
                          className="inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-lg border border-foreground/[0.08] bg-foreground/[0.03] px-2 py-1 text-caption text-muted-foreground"
                        >
                          <PendingFilePreview file={file} />
                          <Icon size="sm" className="shrink-0" />
                          <span className="truncate">{file.name}</span>
                          <span className="font-mono text-[9px] text-muted-foreground/50">
                            {formatFileSize(file.size)}
                          </span>
                          <button
                            type="button"
                            onClick={() => removeSelectedFile(index)}
                            title="Remove"
                            className="text-muted-foreground/50 hover:text-foreground"
                          >
                            <XIcon size="sm" />
                          </button>
                        </span>
                      );
                    })}
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
                  <button
                    type="button"
                    onClick={handleSave}
                    disabled={!canSave || saving}
                    className={cn(
                      "rounded-lg px-5 py-2 text-[9px] uppercase tracking-[0.12em] font-mono font-semibold transition-all",
                      !canSave || saving
                        ? "bg-foreground/[0.06] text-muted-foreground/50 cursor-not-allowed"
                        : "bg-accent text-accent-foreground shadow-[0_2px_10px_rgba(0,0,0,0.25)] hover:brightness-110 active:scale-95",
                    )}
                  >
                    {saving ? "Saving" : isEditingSelected ? "Save changes" : "Save entry"}
                  </button>
                </div>
              </div>
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
