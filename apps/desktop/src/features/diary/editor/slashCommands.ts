import type { Editor, Range } from "@tiptap/core";

export interface SlashCommand {
  id: string;
  label: string;
  hint: string;
  group: "basic" | "media";
  run: (editor: Editor, range: Range) => void;
}

export const SLASH_COMMANDS: SlashCommand[] = [
  {
    id: "paragraph",
    label: "Text",
    hint: "",
    group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).setParagraph().run(),
  },
  {
    id: "h1",
    label: "Heading 1",
    hint: "#",
    group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).setNode("heading", { level: 1 }).run(),
  },
  {
    id: "h2",
    label: "Heading 2",
    hint: "##",
    group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).setNode("heading", { level: 2 }).run(),
  },
  {
    id: "h3",
    label: "Heading 3",
    hint: "###",
    group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).setNode("heading", { level: 3 }).run(),
  },
  {
    id: "bullet",
    label: "Bullet list",
    hint: "-",
    group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleBulletList().run(),
  },
  {
    id: "ordered",
    label: "Numbered list",
    hint: "1.",
    group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleOrderedList().run(),
  },
  {
    id: "task",
    label: "To-do list",
    hint: "[]",
    group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleTaskList().run(),
  },
  {
    id: "quote",
    label: "Quote",
    hint: ">",
    group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleBlockquote().run(),
  },
  {
    id: "code",
    label: "Code block",
    hint: "```",
    group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleCodeBlock().run(),
  },
  {
    id: "divider",
    label: "Divider",
    hint: "---",
    group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).setHorizontalRule().run(),
  },
  {
    id: "table",
    label: "Table",
    hint: "",
    group: "basic",
    run: (e, r) =>
      e.chain().focus().deleteRange(r).insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(),
  },
  {
    id: "toggle",
    label: "Toggle",
    hint: "",
    group: "basic",
    run: (e, r) => e.chain().focus().deleteRange(r).setDetails().run(),
  },
  // NOTE: a "callout" command (setCallout) is intentionally omitted here.
  // setCallout does not exist until Task 8 adds the Callout extension —
  // adding this entry now would fail to typecheck. Task 8 restores it.
  {
    id: "image",
    label: "Image",
    hint: "",
    group: "media",
    // Handled by the menu renderer, which calls onImageRequest — inserting
    // needs an async upload, so it cannot be a synchronous editor command.
    run: (e, r) => e.chain().focus().deleteRange(r).run(),
  },
];

export function filterSlashCommands(commands: SlashCommand[], query: string): SlashCommand[] {
  const q = query.trim().toLowerCase();
  if (!q) return commands;
  return commands.filter(
    (c) => c.label.toLowerCase().includes(q) || (c.hint !== "" && c.hint.toLowerCase().startsWith(q)),
  );
}
