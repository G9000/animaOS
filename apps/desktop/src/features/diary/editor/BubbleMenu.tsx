import { BubbleMenu } from "@tiptap/react/menus";
import type { Editor } from "@tiptap/react";
import { cn } from "@anima/standard-templates";
import {
  BoldGlyphIcon,
  CodeGlyphIcon,
  ItalicGlyphIcon,
  LinkGlyphIcon,
  StrikeGlyphIcon,
} from "./glyphIcons";

type HighlightTone = "amber" | "mint" | "lilac";

const HIGHLIGHT_TONES: HighlightTone[] = ["amber", "mint", "lilac"];

function toggleHighlightTone(editor: Editor, tone: HighlightTone) {
  // Highlight's upstream `declare module` augmentation fixes
  // toggleHighlight's attributes type to `{ color: string }`. The `tone`
  // attribute is a custom addition registered in editor/extensions.ts and
  // isn't reflected there, so the object literal needs a cast to satisfy
  // the stale type. At runtime the command only cares that the object's
  // keys match the attribute names declared via addAttributes ("tone"),
  // so this changes nothing about behavior — it only silences the
  // mismatched upstream type.
  editor
    .chain()
    .focus()
    .toggleHighlight({ tone } as unknown as { color: string })
    .run();
}

function ToolbarButton({
  onClick,
  isActive,
  title,
  children,
}: {
  onClick: () => void;
  isActive: boolean;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
      title={title}
      className={cn("diary-bubble-menu-btn", isActive && "is-active")}
    >
      {children}
    </button>
  );
}

export function DiaryBubbleMenu({ editor }: { editor: Editor }) {
  if (!editor) return null;

  const handleSetLink = () => {
    const previousUrl = (editor.getAttributes("link").href as string | undefined) ?? "";
    const url = window.prompt("Link URL", previousUrl);
    if (url === null) return;
    const trimmed = url.trim();
    if (!trimmed) {
      editor.chain().focus().extendMarkRange("link").unsetLink().run();
      return;
    }
    editor.chain().focus().extendMarkRange("link").setLink({ href: trimmed }).run();
  };

  return (
    <BubbleMenu
      editor={editor}
      shouldShow={({ editor, from, to }) => from !== to && !editor.isActive("codeBlock")}
      className="diary-bubble-menu"
    >
      <div className="diary-bubble-menu-group">
        <ToolbarButton
          onClick={() => editor.chain().focus().toggleBold().run()}
          isActive={editor.isActive("bold")}
          title="Bold"
        >
          <BoldGlyphIcon />
        </ToolbarButton>
        <ToolbarButton
          onClick={() => editor.chain().focus().toggleItalic().run()}
          isActive={editor.isActive("italic")}
          title="Italic"
        >
          <ItalicGlyphIcon />
        </ToolbarButton>
        <ToolbarButton
          onClick={() => editor.chain().focus().toggleStrike().run()}
          isActive={editor.isActive("strike")}
          title="Strikethrough"
        >
          <StrikeGlyphIcon />
        </ToolbarButton>
        <ToolbarButton
          onClick={() => editor.chain().focus().toggleCode().run()}
          isActive={editor.isActive("code")}
          title="Inline code"
        >
          <CodeGlyphIcon />
        </ToolbarButton>
        <ToolbarButton onClick={handleSetLink} isActive={editor.isActive("link")} title="Link">
          <LinkGlyphIcon />
        </ToolbarButton>
      </div>
      <div className="diary-bubble-menu-group">
        {HIGHLIGHT_TONES.map((tone) => (
          <button
            key={tone}
            type="button"
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => toggleHighlightTone(editor, tone)}
            title={`Highlight — ${tone}`}
            className={cn(
              "diary-bubble-menu-tone",
              `diary-bubble-menu-tone-${tone}`,
              editor.isActive("highlight", { tone }) && "is-active",
            )}
          />
        ))}
      </div>
    </BubbleMenu>
  );
}
