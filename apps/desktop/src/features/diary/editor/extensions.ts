import { Extension } from "@tiptap/core";
import type { Extensions } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { Placeholder } from "@tiptap/extensions";
import TiptapImage from "@tiptap/extension-image";
import { TaskItem, TaskList } from "@tiptap/extension-list";
import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import { Details, DetailsContent, DetailsSummary } from "@tiptap/extension-details";
import Highlight from "@tiptap/extension-highlight";
import CodeBlockLowlight from "@tiptap/extension-code-block-lowlight";
import { createLowlight, common } from "lowlight";
import Suggestion from "@tiptap/suggestion";
import { SLASH_COMMANDS, filterSlashCommands } from "./slashCommands";
import { createSlashRenderer } from "./SlashMenu";

const lowlight = createLowlight(common);

interface SlashCommandsOptions {
  onImageRequest?: () => void;
}

// The slash-menu commands themselves have no editor commands to insert an
// image synchronously (the actual insertion needs an async file read /
// upload), so the "image" item is special-cased: it deletes the trigger text
// and defers to onImageRequest, which the host wires to its file picker.
const SlashCommands = Extension.create<SlashCommandsOptions>({
  name: "diarySlashCommands",
  addOptions() {
    return { onImageRequest: undefined };
  },
  addProseMirrorPlugins() {
    return [
      Suggestion({
        editor: this.editor,
        char: "/",
        startOfLine: false,
        allowSpaces: false,
        items: ({ query }) => filterSlashCommands(SLASH_COMMANDS, query),
        command: ({ editor, range, props }) => {
          if (props.id === "image") {
            editor.chain().focus().deleteRange(range).run();
            this.options.onImageRequest?.();
            return;
          }
          props.run(editor, range);
        },
        render: createSlashRenderer,
      }),
    ];
  },
});

export interface DiaryExtensionOptions {
  placeholder?: string;
  onImageRequest?: () => void;
}

export function createDiaryExtensions(options: DiaryExtensionOptions = {}): Extensions {
  return [
    // codeBlock is replaced by the lowlight variant; registering both throws
    // on duplicate node names. Link and Underline already ship in StarterKit.
    StarterKit.configure({
      codeBlock: false,
      heading: { levels: [1, 2, 3] },
    }),
    Placeholder.configure({
      placeholder: options.placeholder ?? "Write, or press / for blocks",
    }),
    TiptapImage.configure({ allowBase64: true }),
    TaskList,
    TaskItem.configure({ nested: true }),
    Table.configure({ resizable: true }),
    TableRow,
    TableHeader,
    TableCell,
    Details,
    DetailsSummary,
    DetailsContent,
    // Tones are carried on data-tone so the sanitizer never needs to allow
    // inline style. multicolor would write style="background-color: …".
    Highlight.extend({
      addAttributes() {
        return {
          tone: {
            default: null,
            parseHTML: (element) => element.getAttribute("data-tone"),
            renderHTML: (attributes) =>
              attributes.tone ? { "data-tone": attributes.tone as string } : {},
          },
        };
      },
    }).configure({ multicolor: false }),
    CodeBlockLowlight.configure({ lowlight }),
    SlashCommands.configure({ onImageRequest: options.onImageRequest }),
  ];
}
