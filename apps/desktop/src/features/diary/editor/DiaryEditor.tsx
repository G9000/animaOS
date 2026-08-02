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
  onImagePaste: (file: File) => Promise<number | null>;
  // Deviation from the brief's literal prop list: a few pre-existing,
  // externally-triggered behaviors — inline-image embedding from a hidden
  // file input owned by the parent (still fed by `onImageRequest`),
  // voice-transcript insertion, and focusing the editor on canvas click —
  // all act on the live Tiptap `Editor` instance from outside this
  // component. Rather than re-plumb each of those through new one-off
  // callback props (and risk changing their behavior), this mirrors the
  // pre-Task-12 `editorRef.current = editor` assignment: the parent is
  // handed the instance once at creation and again (as null) at teardown.
  // `editor/` still never imports the API client — this only ever crosses
  // the Editor object itself, never a network call.
  onEditorReady?: (editor: Editor | null) => void;
}

export function DiaryEditor(props: DiaryEditorProps) {
  const { onChange, onImageRequest, onImagePaste, onEditorReady } = props;
  const editorRef = useRef<Editor | null>(null);

  const editor = useEditor({
    extensions: createDiaryExtensions({
      placeholder: "Write your thoughts… ( '/' for commands · drop or paste an image )",
      onImageRequest,
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
            // Pasted image FILES (e.g. copied from Finder) become
            // attachments via the parent's onImagePaste, same as the
            // "Attach" button — distinct from the slash "/image" command's
            // inline base64 embed, which goes through onImageRequest.
            for (const file of imageFiles) void onImagePaste(file);
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
    },
    onCreate: ({ editor: created }) => {
      onEditorReady?.(created);
      window.setTimeout(() => created.commands.focus("end"), 0);
    },
    onDestroy: () => {
      onEditorReady?.(null);
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
