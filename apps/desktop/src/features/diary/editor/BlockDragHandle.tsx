import { useEffect, useRef, useState, type ReactNode } from "react";
import { DragHandle } from "@tiptap/extension-drag-handle-react";
import type { Editor } from "@tiptap/react";
import type { Node as ProseMirrorNode } from "@tiptap/pm/model";
import { cn } from "@anima/standard-templates";
import { SLASH_COMMANDS, type SlashCommand } from "./slashCommands";
import {
  BulletListGlyphIcon,
  CalloutGlyphIcon,
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
  callout: <CalloutGlyphIcon />,
};

export interface HoveredNode {
  node: ProseMirrorNode;
  pos: number;
}

// Finding 1 (Task 7 review, round 1): insertTable/setHorizontalRule replace
// the whole selection instead of changing the block's type, so running
// them over a non-empty block silently discards its text. These two are
// the only Turn-into entries with that shape — everything else is a
// setNode/toggle command that changes type in place.
const DESTRUCTIVE_TURN_INTO_IDS = new Set(["table", "divider"]);

// Finding 2: recognizes the hovered node when it is ALREADY the command's
// target type, so "Turn into X" is idempotent instead of toggling X off
// (toggleBulletList on a bulletList, toggleBlockquote on a blockquote,
// etc. would otherwise unwrap it). Only used for the non-destructive
// commands — table/divider aren't toggles, so "already a table" isn't a
// meaningful no-op case for them.
function matchesTargetType(node: ProseMirrorNode, command: SlashCommand): boolean {
  switch (command.id) {
    case "paragraph":
      return node.type.name === "paragraph";
    case "h1":
      return node.type.name === "heading" && node.attrs.level === 1;
    case "h2":
      return node.type.name === "heading" && node.attrs.level === 2;
    case "h3":
      return node.type.name === "heading" && node.attrs.level === 3;
    case "bullet":
      return node.type.name === "bulletList";
    case "ordered":
      return node.type.name === "orderedList";
    case "task":
      return node.type.name === "taskList";
    case "quote":
      return node.type.name === "blockquote";
    case "code":
      return node.type.name === "codeBlock";
    case "toggle":
      return node.type.name === "details";
    case "callout":
      return node.type.name === "callout";
    default:
      return false;
  }
}

// Finding 3: @tiptap/extension-drag-handle calls getOuterNode/getOuterDomNode
// when `nested` isn't enabled (the default here), so hovering text inside a
// wrapper like blockquote reports the wrapper itself as the hovered node,
// not its inner paragraph. setNode/toggle commands only ever change the
// nearest *textblock* ancestor's type — they never strip a surrounding
// non-textblock wrapper — so running them against the wrapper's own
// selection leaves the result nested inside it (e.g.
// <blockquote><h1>...</h1></blockquote> instead of a top-level <h1>).
// This recognizes that shape (a container whose direct children are all
// plain textblocks, e.g. blockquote wrapping paragraph(s)) so the caller
// can lift the content out first. Deliberately narrow: list wrappers
// (bulletList/orderedList/taskList) hold listItem children, which are
// themselves containers, not textblocks, so this correctly leaves list
// conversion untouched — not something any finding asked to change.
function isSimpleTextblockWrapper(node: ProseMirrorNode): boolean {
  if (node.isTextblock || node.isAtom || node.childCount === 0) return false;
  for (let i = 0; i < node.childCount; i += 1) {
    if (!node.child(i).isTextblock) return false;
  }
  return true;
}

// Finding 3 (PR #139 round 8): pulled out of `BlockDragHandle`'s
// `handleTurnInto` closure so it can be exercised directly against a real
// `Editor` + real document in tests — asserting on the resulting document,
// not a return value — without needing to render the `DragHandle` UI
// (which needs live DOM positions/floating-ui) just to prove the fix.
// Behavior is unchanged from the original inline version, aside from the
// atom guard called out below.
export function performTurnInto(
  editor: Editor,
  hovered: HoveredNode,
  command: SlashCommand,
  confirm: (message: string) => boolean,
): void {
  const { node: hoveredNode, pos: hoveredPos } = hovered;

  if (DESTRUCTIVE_TURN_INTO_IDS.has(command.id)) {
    // Finding 1: only prompt when there's actually something to lose — an
    // empty block converting to a table/divider has no text for the
    // dialog's wording to be honest about, and shouldn't interrupt the
    // user for nothing.
    if (hoveredNode.textContent.trim().length > 0) {
      const noun = command.id === "table" ? "a table" : "a divider";
      const confirmed = confirm(`Converting to ${noun} will discard this block's text. Continue?`);
      // On cancel: bail out before touching the editor at all, so there's
      // no partial transaction, no moved selection, no leftover node — the
      // document is byte-identical to before the click.
      if (!confirmed) return;
    }
    // Confirmed (or nothing to lose): replace the whole hovered block,
    // wrapper included — that's the point of these two conversions, so no
    // lift/preserve trick applies here. This branch already addresses the
    // hovered node's own range directly (`hoveredPos` .. `hoveredPos +
    // hoveredNode.nodeSize`), so it is unaffected by the atom hazard below
    // — it never constructs a text selection past the node's end.
    command.run(editor, { from: hoveredPos, to: hoveredPos + hoveredNode.nodeSize });
    return;
  }

  // Finding 3 (PR #139 round 8): an atom (inline image, horizontal rule)
  // has `nodeSize === 1` — there is no position *inside* it for a text
  // selection to occupy. The collapsed-selection trick further down
  // computes `from = pos + 1`, which for an atom is the position
  // immediately AFTER it, not inside it — so `setTextSelection` would
  // silently land the cursor in whatever follows (typically the next
  // block's start), and the `command.run(...)` below would then convert
  // THAT block instead of leaving the atom untouched. "Turn into" a
  // textblock type is not a meaningful operation on an atom in the first
  // place (there's no text to carry over), so it's refused outright — the
  // submenu in BlockDragHandle also hides this action while hovering an
  // atom; this is defense-in-depth for any direct caller.
  if (hoveredNode.isAtom) return;

  // Finding 2: "Turn into X" must always yield X, never toggle away from
  // it — toggleBulletList()/toggleBlockquote()/etc. are correct as
  // *toggles* in the slash-menu flow (Task 5), but here the user picked an
  // explicit target. If the hovered block is already that type, this is a
  // no-op: do nothing rather than calling a toggle command that would flip
  // it back to a paragraph.
  if (matchesTargetType(hoveredNode, command)) return;

  let node = hoveredNode;
  let pos = hoveredPos;

  if (isSimpleTextblockWrapper(node)) {
    // Finding 3 (Task 7): lift the wrapper's (first) child out to a
    // top-level position before converting it, so the result isn't left
    // nested inside the original wrapper.
    const child = node.firstChild;
    if (!child) return;
    const childPos = pos + 1;
    const from = childPos + 1;
    const to = Math.max(from, childPos + child.nodeSize - 1);
    const lifted = editor.chain().focus().setTextSelection({ from, to }).lift(child.type.name).run();
    if (!lifted) return;
    // Lifting a single-child wrapper removes exactly its own opening
    // token, so the child's own start shifts left by one slot, landing it
    // at the wrapper's old `pos` — verified against a live Editor instance
    // (see task-7-report.md) rather than assumed.
    const liftedNode = editor.state.doc.nodeAt(pos);
    if (!liftedNode) return;
    node = liftedNode;
  }

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
  // task-7-report.md) for paragraph/heading/code-block conversions. Safe
  // from the atom hazard above: by this point `node` is guaranteed to be a
  // (possibly lifted) textblock or textblock-wrapper child, never an atom.
  const from = pos + 1;
  const to = Math.max(from, pos + node.nodeSize - 1);
  editor.chain().focus().setTextSelection({ from, to }).run();
  command.run(editor, { from, to: from });
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
    performTurnInto(editor, hovered, command, (message) => window.confirm(message));
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
            {
              // Finding 3 (PR #139 round 8): an atom (inline image,
              // horizontal rule) has no "inside" for any of these commands
              // to convert — hiding the whole submenu while hovering one
              // is the chosen fix, rather than trying to make the
              // collapsed-selection trick work against a position that
              // doesn't exist. See performTurnInto's own guard above for
              // the defense-in-depth check backing this up.
              !hovered?.node.isAtom && turnIntoOpen && (
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
              )
            }
          </div>
        </div>
      )}
    </DragHandle>
  );
}
