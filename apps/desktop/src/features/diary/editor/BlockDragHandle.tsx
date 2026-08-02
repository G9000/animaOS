import { useEffect, useRef, useState, type ReactNode } from "react";
import { DragHandle } from "@tiptap/extension-drag-handle-react";
import type { Editor } from "@tiptap/react";
import type { Node as ProseMirrorNode } from "@tiptap/pm/model";
import { cn } from "@anima/standard-templates";
import { SLASH_COMMANDS, type SlashCommand } from "./slashCommands";
import {
  BulletListGlyphIcon,
  CodeGlyphIcon,
  DividerGlyphIcon,
  EllipsisGlyphIcon,
  GripGlyphIcon,
  HeadingGlyphIcon,
  OrderedListGlyphIcon,
  ParagraphGlyphIcon,
  QuoteGlyphIcon,
  TableGlyphIcon,
  TaskListGlyphIcon,
  ToggleGlyphIcon,
} from "./glyphIcons";

// "Turn into" reuses the same basic-block commands the slash menu offers
// (Task 5) instead of a second parallel list — see slashCommands.ts.
const TURN_INTO_COMMANDS = SLASH_COMMANDS.filter((command) => command.group === "basic");

const TURN_INTO_ICONS: Record<string, ReactNode> = {
  paragraph: <ParagraphGlyphIcon />,
  h1: <HeadingGlyphIcon level={1} />,
  h2: <HeadingGlyphIcon level={2} />,
  h3: <HeadingGlyphIcon level={3} />,
  bullet: <BulletListGlyphIcon />,
  ordered: <OrderedListGlyphIcon />,
  task: <TaskListGlyphIcon />,
  quote: <QuoteGlyphIcon />,
  code: <CodeGlyphIcon />,
  divider: <DividerGlyphIcon />,
  table: <TableGlyphIcon />,
  toggle: <ToggleGlyphIcon />,
};

interface HoveredNode {
  node: ProseMirrorNode;
  pos: number;
}

// A button nested inside the handle's own draggable container needs its own
// `draggable={false}` — the container itself is made draggable by the
// DragHandle plugin, and per the HTML drag-and-drop spec the nearest
// explicit `draggable` value (including on a descendant) wins over an
// ancestor's, so this is what keeps clicking the kebab/menu from also
// starting a block drag.
function NonDraggableButton({
  onClick,
  className,
  title,
  children,
}: {
  onClick: (event: React.MouseEvent) => void;
  className?: string;
  title?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      draggable={false}
      title={title}
      onMouseDown={(event) => {
        event.preventDefault();
        event.stopPropagation();
      }}
      onClick={(event) => {
        event.stopPropagation();
        onClick(event);
      }}
      className={className}
    >
      {children}
    </button>
  );
}

export function BlockDragHandle({ editor }: { editor: Editor }) {
  const [hovered, setHovered] = useState<HoveredNode | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [turnIntoOpen, setTurnIntoOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const closeMenus = () => {
    setMenuOpen(false);
    setTurnIntoOpen(false);
  };

  useEffect(() => {
    if (!menuOpen) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        closeMenus();
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [menuOpen]);

  // The hovered node's position becomes stale the moment the handle closes
  // (the next hover may target a different block), so menus tied to it
  // shouldn't outlive it.
  useEffect(() => {
    if (!hovered) closeMenus();
  }, [hovered]);

  if (!editor) return null;

  const handleDuplicate = () => {
    if (!hovered) return;
    const { node, pos } = hovered;
    editor.chain().focus().insertContentAt(pos + node.nodeSize, node.toJSON()).run();
    closeMenus();
  };

  const handleDelete = () => {
    if (!hovered) return;
    const { node, pos } = hovered;
    editor.chain().focus().deleteRange({ from: pos, to: pos + node.nodeSize }).run();
    closeMenus();
  };

  const handleTurnInto = (command: SlashCommand) => {
    if (!hovered) return;
    const { node, pos } = hovered;
    // SlashCommand.run always starts with `deleteRange(range)` — that's
    // correct for the slash-menu flow, where `range` is the typed "/query"
    // text that needs erasing before the block command runs. Reusing it
    // here with the hovered node's own range (`{ from: pos, to: pos +
    // node.nodeSize }`) would delete the block's content before the
    // conversion command ever ran. Instead, move the selection onto the
    // node's inline content and pass a *collapsed* range at that same spot:
    // deleteRange then deletes nothing, and the setNode/toggle command that
    // follows acts on the selection already in place, converting the block
    // while keeping its text. Verified against a live Editor instance (see
    // task-7-report.md) for paragraph/heading/code-block conversions.
    const from = pos + 1;
    const to = Math.max(from, pos + node.nodeSize - 1);
    editor.chain().focus().setTextSelection({ from, to }).run();
    command.run(editor, { from, to: from });
    closeMenus();
  };

  return (
    <DragHandle
      editor={editor}
      className="diary-drag-handle"
      onNodeChange={({ node, pos }) => setHovered(node ? { node, pos } : null)}
    >
      <div className="diary-drag-handle-buttons">
        <button
          type="button"
          tabIndex={-1}
          title="Drag to move"
          className="diary-drag-handle-btn diary-drag-handle-grip"
        >
          <GripGlyphIcon />
        </button>
        <NonDraggableButton
          title="Block actions"
          className={cn("diary-drag-handle-btn", menuOpen && "is-active")}
          onClick={() => setMenuOpen((open) => !open)}
        >
          <EllipsisGlyphIcon />
        </NonDraggableButton>
      </div>
      {menuOpen && (
        <div ref={menuRef} className="diary-drag-handle-menu" draggable={false}>
          <NonDraggableButton
            className={cn("diary-drag-handle-menu-item", "text-detail")}
            onClick={handleDuplicate}
          >
            Duplicate
          </NonDraggableButton>
          <NonDraggableButton
            className={cn("diary-drag-handle-menu-item", "text-detail")}
            onClick={handleDelete}
          >
            Delete
          </NonDraggableButton>
          <div
            className="diary-drag-handle-submenu-trigger"
            onMouseEnter={() => setTurnIntoOpen(true)}
            onMouseLeave={() => setTurnIntoOpen(false)}
          >
            <NonDraggableButton
              className={cn("diary-drag-handle-menu-item", "text-detail")}
              onClick={() => setTurnIntoOpen((open) => !open)}
            >
              Turn into
            </NonDraggableButton>
            {turnIntoOpen && (
              <div className="diary-drag-handle-submenu" draggable={false}>
                {TURN_INTO_COMMANDS.map((command) => (
                  <NonDraggableButton
                    key={command.id}
                    className={cn("diary-drag-handle-menu-item", "text-detail")}
                    onClick={() => handleTurnInto(command)}
                  >
                    {TURN_INTO_ICONS[command.id]}
                    <span>{command.label}</span>
                  </NonDraggableButton>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </DragHandle>
  );
}
