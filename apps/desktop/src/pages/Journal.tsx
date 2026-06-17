import { useEffect, useMemo, useRef, useState } from "react";
import {
  FileIcon,
  ImageIcon,
  MicIcon,
  PlusIcon,
  XIcon,
  cn,
} from "@anima/standard-templates";
import type { DiaryAttachmentData, DiaryEntryData } from "@anima/api-client";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Placeholder } from "@tiptap/extensions";
import { useAuth } from "../context/AuthContext";
import { api } from "../lib/api";
import {
  buildRecordingFilename,
  chooseRecordingMimeType,
  getSpeechRecognitionConstructor,
  type SpeechRecognitionLike,
} from "./journal/speech";

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

function entryExcerpt(entry: DiaryEntryData): string {
  const text = plainTextOfBody(entry.body).replace(/\s+/g, " ").trim();
  return text.length > 90 ? `${text.slice(0, 90)}…` : text;
}

function countWords(text: string): number {
  const trimmed = text.trim();
  return trimmed ? trimmed.split(/\s+/).length : 0;
}

export default function Journal() {
  const { user } = useAuth();
  const [entries, setEntries] = useState<DiaryEntryData[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [entryDate, setEntryDate] = useState(todayISODate);
  const [title, setTitle] = useState("");
  const [bodyText, setBodyText] = useState("");
  const [mood, setMood] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [recording, setRecording] = useState(false);
  const [speechAvailable, setSpeechAvailable] = useState(false);
  const [liveTranscript, setLiveTranscript] = useState("");
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const recordedChunksRef = useRef<BlobPart[]>([]);

  const editor = useEditor({
    extensions: [
      StarterKit,
      Placeholder.configure({
        placeholder: "Write your thoughts… ('#' heading, '-' list, '>' quote)",
      }),
    ],
    editorProps: {
      attributes: {
        class: "tiptap prose max-w-none min-h-[40vh] text-base leading-loose",
      },
    },
    onUpdate: ({ editor: e }) => {
      setBodyText(e.getText());
    },
  });
  const editorRef = useRef(editor);
  editorRef.current = editor;

  const selectedEntry = useMemo(
    () => entries.find((entry) => entry.id === selectedId) ?? null,
    [entries, selectedId],
  );
  const wordCount = useMemo(() => countWords(bodyText), [bodyText]);
  const canSave = bodyText.trim().length > 0 || files.length > 0;

  const loadEntries = async () => {
    if (user?.id == null) return;
    setLoading(true);
    try {
      const diaryEntries = await api.diary.list(user.id, 100);
      setEntries(diaryEntries);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load diary.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadEntries();
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
    editorRef.current?.commands.clearContent();
    setMood("");
    setFiles([]);
    setEntryDate(todayISODate());
  };

  const startNewEntry = () => {
    setSelectedId(null);
    resetComposer();
    window.setTimeout(() => editorRef.current?.commands.focus("end"), 0);
  };

  const handleFilesSelected = (selected: FileList | null) => {
    if (!selected) return;
    setFiles((current) => [...current, ...Array.from(selected)]);
  };

  const removeSelectedFile = (index: number) => {
    setFiles((current) => current.filter((_, i) => i !== index));
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
          setBodyText(ed.getText());
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

  const handleCreate = async () => {
    if (user?.id == null || !canSave || saving) return;
    if (recording) {
      stopRecording();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const hasText = bodyText.trim().length > 0;
      const entry = await api.diary.create(user.id, {
        entryDate,
        title: title.trim() || null,
        body: hasText
          ? (editorRef.current?.getHTML() ?? bodyText.trim())
          : "Attachment-only diary entry.",
        mood: mood.trim() || null,
      });

      for (const file of files) {
        await api.diary.uploadAttachment(entry.id, file);
      }

      resetComposer();
      await loadEntries();
      setSelectedId(entry.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save diary entry.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (entryId: number) => {
    if (!confirm("Delete this diary entry?")) return;
    try {
      await api.diary.delete(entryId);
      setEntries((current) => current.filter((entry) => entry.id !== entryId));
      if (selectedId === entryId) {
        setSelectedId(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete diary entry.");
    }
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

  return (
    <div className="h-full pt-16 flex overflow-hidden">
      {/* Sidebar — entry list */}
      <aside className="w-72 shrink-0 border-r border-border flex flex-col bg-card/40">
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-border">
          <div>
            <h1 className="text-ui font-semibold text-foreground">Diary</h1>
            <p className="font-mono text-[9px] tracking-[0.22em] uppercase text-muted-foreground/50 mt-0.5">
              {entries.length} {entries.length === 1 ? "entry" : "entries"}
            </p>
          </div>
          <button
            type="button"
            onClick={startNewEntry}
            title="New entry"
            className="hw-btn h-8 w-8 text-muted-foreground hover:text-foreground"
          >
            <PlusIcon size="sm" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center gap-1.5 py-12 justify-center">
              <span className="w-1 h-1 bg-muted-foreground/40 animate-pulse" />
              <span className="w-1 h-1 bg-muted-foreground/40 animate-pulse [animation-delay:150ms]" />
              <span className="w-1 h-1 bg-muted-foreground/40 animate-pulse [animation-delay:300ms]" />
            </div>
          ) : entries.length === 0 ? (
            <p className="px-4 py-10 text-center font-mono text-[10px] tracking-[0.2em] uppercase text-muted-foreground/40">
              No entries yet
            </p>
          ) : (
            <ul>
              {entries.map((entry) => (
                <li key={entry.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(entry.id)}
                    className={cn(
                      "w-full text-left px-4 py-3 border-b border-border/60 transition-colors",
                      selectedId === entry.id
                        ? "bg-secondary"
                        : "hover:bg-secondary/50",
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[9px] tracking-[0.2em] uppercase text-muted-foreground/60">
                        {formatEntryDate(entry.entryDate)}
                      </span>
                      {entry.mood && (
                        <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground/50 truncate">
                          · {entry.mood}
                        </span>
                      )}
                      {entry.attachments.length > 0 && (
                        <span className="ml-auto font-mono text-[9px] text-muted-foreground/40 shrink-0">
                          {entry.attachments.length} ⊕
                        </span>
                      )}
                    </div>
                    {entry.title && (
                      <p className="mt-1 text-body font-medium text-foreground truncate">
                        {entry.title}
                      </p>
                    )}
                    <p className="mt-0.5 text-detail text-muted-foreground line-clamp-2">
                      {entryExcerpt(entry)}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>

      {/* Canvas — write or read */}
      <main className="flex-1 min-w-0 flex flex-col">
        {error && (
          <div className="mx-8 mt-4 border border-destructive/30 bg-destructive/10 px-3 py-2 text-detail text-destructive">
            {error}
          </div>
        )}

        {selectedEntry ? (
          /* Read mode */
          <div className="flex-1 overflow-y-auto">
            <div className="max-w-3xl mx-auto px-8 py-10">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="font-mono text-[10px] tracking-[0.22em] uppercase text-muted-foreground/60">
                    {formatEntryDateLong(selectedEntry.entryDate)}
                  </p>
                  {selectedEntry.mood && (
                    <span className="mt-2 inline-block border border-border bg-secondary px-2 py-0.5 font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground">
                      {selectedEntry.mood}
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => handleDelete(selectedEntry.id)}
                  title="Delete entry"
                  className="text-muted-foreground/40 hover:text-destructive shrink-0"
                >
                  <XIcon size="sm" />
                </button>
              </div>

              {selectedEntry.title && (
                <h2 className="mt-4 text-2xl font-semibold text-foreground">
                  {selectedEntry.title}
                </h2>
              )}

              {isHtmlBody(selectedEntry.body) ? (
                <div
                  className="prose max-w-none mt-5 text-base leading-loose"
                  // Diary bodies are authored locally in the Tiptap editor and
                  // stored encrypted; schema-constrained HTML, own content only.
                  dangerouslySetInnerHTML={{ __html: selectedEntry.body }}
                />
              ) : (
                <p className="mt-5 whitespace-pre-wrap text-base leading-loose text-foreground">
                  {selectedEntry.body}
                </p>
              )}

              {selectedEntry.attachments.length > 0 && (
                <div className="mt-8 flex flex-wrap gap-1.5">
                  {selectedEntry.attachments.map((attachment) => {
                    const Icon = attachmentIcon(attachment.kind);
                    return (
                      <button
                        key={attachment.id}
                        type="button"
                        onClick={() => void handleOpenAttachment(attachment)}
                        className="inline-flex max-w-full items-center gap-1.5 border border-border bg-secondary px-2 py-1 text-caption text-muted-foreground hover:text-foreground hover:border-muted-foreground/40"
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
          </div>
        ) : (
          /* Write mode */
          <div className="flex-1 min-h-0 flex flex-col">
            <div className="flex-1 min-h-0 overflow-y-auto">
              <div className="max-w-3xl mx-auto px-8 pt-10 pb-4 h-full flex flex-col">
                <div className="flex items-center gap-3">
                  <input
                    type="date"
                    value={entryDate}
                    onChange={(event) => setEntryDate(event.target.value)}
                    className="bg-transparent font-mono text-[10px] tracking-[0.18em] uppercase text-muted-foreground/70 outline-none cursor-pointer hover:text-foreground"
                  />
                  <span className="text-muted-foreground/30">·</span>
                  <input
                    type="text"
                    value={mood}
                    onChange={(event) => setMood(event.target.value)}
                    placeholder="Mood"
                    maxLength={80}
                    className="bg-transparent font-mono text-[10px] tracking-[0.18em] uppercase text-muted-foreground/70 placeholder:text-muted-foreground/30 outline-none w-40"
                  />
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
                  placeholder="Title"
                  maxLength={200}
                  className="mt-5 w-full bg-transparent text-2xl font-semibold text-foreground placeholder:text-muted-foreground/25 outline-none"
                />

                <div
                  className="mt-4 flex-1 min-h-[40vh] cursor-text"
                  onClick={() => editor?.commands.focus()}
                >
                  <EditorContent editor={editor} />
                </div>
              </div>
            </div>

            {/* Bottom toolbar */}
            <div className="border-t border-border bg-card/40">
              <div className="max-w-3xl mx-auto px-8 py-3 space-y-2">
                {recording && (
                  <div className="border border-border bg-secondary px-2 py-1.5 font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground">
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
                          className="inline-flex min-w-0 max-w-full items-center gap-1.5 border border-border bg-secondary px-2 py-1 text-caption text-muted-foreground"
                        >
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
                    <label className="hw-btn cursor-pointer px-3 py-2 text-[9px] text-muted-foreground hover:text-foreground">
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
                    <button
                      type="button"
                      onClick={() => void (recording ? stopRecording() : startRecording())}
                      className={cn(
                        "hw-btn px-3 py-2 text-[9px]",
                        recording
                          ? "border-destructive/40 text-destructive bg-destructive/10"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      <MicIcon size="sm" className="mr-2" />
                      {recording ? "Stop" : "Record"}
                    </button>
                  </div>
                  <button
                    type="button"
                    onClick={handleCreate}
                    disabled={!canSave || saving}
                    className={cn(
                      "hw-btn px-4 py-2 text-[9px]",
                      !canSave || saving
                        ? "opacity-40 cursor-not-allowed"
                        : "text-foreground hover:border-primary/40",
                    )}
                  >
                    {saving ? "Saving" : "Save entry"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
