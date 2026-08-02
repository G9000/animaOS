import { useRef } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import type { Editor } from "@tiptap/react";
import { cn } from "@anima/standard-templates";
import { marked } from "marked";
import { createDiaryHtmlSanitizer } from "../lib/sanitize";
import { createDiaryExtensions } from "./extensions";
import { DiaryBubbleMenu } from "./BubbleMenu";
import { BlockDragHandle } from "./BlockDragHandle";

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

// Instantiated once per DiaryEditor mount (a fresh instance per entryId,
// since the parent keys this component by entry.id — see the boundary note
// on DiaryEditorProps below). createDiaryHtmlSanitizer is a pure factory
// (apps/desktop/src/features/diary/lib/sanitize.ts) with no shared state,
// so a second instance alongside DiaryWorkspace's own (used to sanitize
// content BEFORE handing it down as `initialHtml`) is safe.
const sanitizeDiaryHtml = createDiaryHtmlSanitizer(window);

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

export interface DiaryEditorProps {
  entryId: number;
  initialHtml: string;
  onChange: (html: string, plainText: string) => void;
  onImageRequest: () => void;
  // Task 13: the diaryImage node's only path to the network (see the doc
  // comment on DiaryImageOptions in nodes/AttachmentImage.tsx). Used for
  // ALL THREE ways an inline image can be inserted — clipboard paste,
  // drag-and-drop, and the slash "/image" file picker (which calls this
  // indirectly, by invoking the `insertAttachmentImage` command on the
  // same live editor instance handed back via onEditorReady) — not just
  // paste, hence the rename from the pre-Task-13 `onImagePaste`.
  onImageUpload: (file: File) => Promise<number | null>;
  // Deviation from the brief's literal prop list: a few pre-existing,
  // externally-triggered behaviors — inline-image embedding from a hidden
  // file input owned by the parent (still fed by `onImageRequest`),
  // voice-transcript insertion, and focusing the editor on canvas click —
  // all act on the live Tiptap `Editor` instance from outside this
  // component. Rather than re-plumb each of those through new one-off
  // callback props (and risk changing their behavior), this mirrors the
  // pre-Task-12 `editorRef.current = editor` assignment: the parent is
  // handed the instance once at creation and told again at teardown.
  // `editor/` still never imports the API client — this only ever crosses
  // the Editor object itself, never a network call.
  //
  // Task 12 review, Finding 1: `onEditorReady` is called ONLY on create
  // (always with a live, non-null instance, plus this component's own
  // fixed `entryId` — see below). Teardown is reported through the
  // separate `onEditorDestroyed` callback, passing the SAME instance that
  // is going away, instead of the previous `onEditorReady(null)` pattern.
  // That mattered because Tiptap's own create/destroy ordering across a
  // keyed remount is NOT guaranteed: the reviewer reproduced the
  // INCOMING editor's `create` firing before the OUTGOING editor's
  // `destroy` (both are scheduled via independent timers deep in
  // @tiptap/react's EditorInstanceManager). A parent that just does
  // `editorRef.current = editor ?? null` on every call would have the
  // outgoing editor's stale teardown null out the ref AFTER the new one
  // already populated it. Passing the actual instance on both ends lets
  // the parent compare identity (`if (editorRef.current === destroyed)
  // editorRef.current = null`) instead of trusting arrival order — correct
  // regardless of which of the two timers fires first.
  onEditorReady?: (editor: Editor, entryId: number) => void;
  onEditorDestroyed?: (editor: Editor) => void;
}

export function DiaryEditor(props: DiaryEditorProps) {
  const { onChange, onImageRequest, onImageUpload, onEditorReady, onEditorDestroyed, entryId } = props;
  const editorRef = useRef<Editor | null>(null);
  // Captured once in onCreate and read in onDestroy, rather than closing
  // over the `editor` variable produced by this same useEditor(...) call
  // (which would be a forward reference to a binding that doesn't exist
  // yet while these options are being constructed).
  const createdInstanceRef = useRef<Editor | null>(null);

  const editor = useEditor({
    extensions: createDiaryExtensions({
      placeholder: "Write your thoughts… ( '/' for commands · drop or paste an image )",
      onImageRequest,
      entryId,
      onImageUpload,
    }),
    content: props.initialHtml,
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
            // Task 13: pasted image FILES (e.g. copied from Finder) are
            // inserted inline as attachment-backed diaryImage nodes — no
            // base64 ever enters the body (see nodes/AttachmentImage.tsx).
            // Uploading happens inside the node's own NodeView once
            // inserted, via the `uploadImage` extension option
            // (DiaryEditor's onImageUpload prop), so there is nothing
            // further to await here.
            for (const file of imageFiles) {
              editorRef.current?.commands.insertAttachmentImage(file);
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
          return true;
        }
        return false;
      },
      // Task 13: mirrors handlePaste's image interception for drag-and-drop
      // FILES (as opposed to dragging existing editor content around,
      // which ProseMirror already handles natively and which this never
      // sees `dataTransfer.files` for). `stopPropagation` is deliberate and
      // load-bearing: DiaryWorkspace's composer wrapper also has a native
      // onDrop (handleComposerDrop) that treats ANY dropped file as a
      // regular attachment via the "Attach" flow. Without stopping
      // propagation here, an image dropped onto the editor would be
      // inserted inline by this handler AND separately uploaded again as a
      // plain attachment by the wrapper's handler, since that listener
      // sits further up the same native DOM event bubble (React's own
      // delegation does not get a chance to run once native propagation is
      // stopped below it). Non-image file drops intentionally return false
      // without stopping propagation, so they keep reaching the wrapper's
      // existing "Attach" behavior unchanged.
      handleDrop: (_view, event) => {
        const files = event.dataTransfer?.files;
        if (!files || files.length === 0) return false;
        const imageFiles = Array.from(files).filter((file) => file.type.startsWith("image/"));
        if (imageFiles.length === 0) return false;
        event.preventDefault();
        event.stopPropagation();
        for (const file of imageFiles) {
          editorRef.current?.commands.insertAttachmentImage(file);
        }
        return true;
      },
    },
    onCreate: ({ editor: created }) => {
      createdInstanceRef.current = created;
      onEditorReady?.(created, entryId);
      window.setTimeout(() => created.commands.focus("end"), 0);
    },
    onDestroy: () => {
      const instance = createdInstanceRef.current;
      if (instance) onEditorDestroyed?.(instance);
    },
    onUpdate: ({ editor: updated }) => {
      const html = sanitizeDiaryHtml(updated.getHTML());
      onChange(html, updated.getText());
    },
  });
  editorRef.current = editor;

  return (
    <>
      <DiaryBubbleMenu editor={editor} />
      <BlockDragHandle editor={editor} />
      <EditorContent editor={editor} />
    </>
  );
}
