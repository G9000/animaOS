import type { ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { SuggestionKeyDownProps, SuggestionProps } from "@tiptap/suggestion";
import { cn, ImageIcon } from "@anima/standard-templates";
import type { SlashCommand } from "./slashCommands";
import {
  BulletListGlyphIcon,
  CodeGlyphIcon,
  DividerGlyphIcon,
  HeadingGlyphIcon,
  OrderedListGlyphIcon,
  ParagraphGlyphIcon,
  QuoteGlyphIcon,
  TableGlyphIcon,
  TaskListGlyphIcon,
  ToggleGlyphIcon,
} from "./glyphIcons";

const COMMAND_ICONS: Record<string, ReactNode> = {
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
  image: <ImageIcon size="sm" />,
};

function SlashMenuList({
  items,
  selectedIndex,
  onSelect,
  onHover,
}: {
  items: SlashCommand[];
  selectedIndex: number;
  onSelect: (item: SlashCommand) => void;
  onHover: (index: number) => void;
}) {
  if (items.length === 0) {
    return <p className="diary-slash-menu-empty">No matching commands</p>;
  }
  return (
    <ul className="diary-slash-menu-list">
      {items.map((item, index) => (
        <li key={item.id}>
          <button
            type="button"
            onMouseDown={(event) => {
              event.preventDefault();
              onSelect(item);
            }}
            onMouseEnter={() => onHover(index)}
            className={cn(
              "diary-slash-menu-item text-detail",
              index === selectedIndex && "is-selected",
            )}
          >
            {COMMAND_ICONS[item.id]}
            <span className="diary-slash-menu-item-label">{item.label}</span>
            <span className="diary-slash-menu-item-hint">{item.hint}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

// Suggestion's lifecycle (onStart/onUpdate/onKeyDown/onExit) is imperative —
// it owns when the popup mounts, repositions and tears down. A React root is
// mounted into a plain DOM node so the menu can still reuse the existing JSX
// and glyph icon components without inventing a parallel markup language.
export function createSlashRenderer() {
  let container: HTMLDivElement | null = null;
  let root: Root | null = null;
  let selectedIndex = 0;
  let current: SuggestionProps<SlashCommand> | null = null;

  const draw = () => {
    if (!root || !current) return;
    const props = current;
    root.render(
      <SlashMenuList
        items={props.items}
        selectedIndex={selectedIndex}
        onSelect={(item) => props.command(item)}
        onHover={(index) => {
          selectedIndex = index;
          draw();
        }}
      />,
    );
  };

  const position = (props: SuggestionProps<SlashCommand>) => {
    const rect = props.clientRect?.();
    if (!rect || !container) return;
    container.style.top = `${rect.bottom + window.scrollY}px`;
    container.style.left = `${rect.left + window.scrollX}px`;
  };

  return {
    onStart(props: SuggestionProps<SlashCommand>) {
      current = props;
      selectedIndex = 0;
      container = document.createElement("div");
      container.className = "diary-slash-menu";
      document.body.appendChild(container);
      root = createRoot(container);
      draw();
      position(props);
    },
    onUpdate(props: SuggestionProps<SlashCommand>) {
      current = props;
      selectedIndex = Math.min(selectedIndex, Math.max(props.items.length - 1, 0));
      draw();
      position(props);
    },
    onKeyDown({ event }: SuggestionKeyDownProps) {
      if (!current) return false;
      const count = current.items.length;
      if (event.key === "ArrowDown") {
        selectedIndex = count ? (selectedIndex + 1) % count : 0;
        draw();
        return true;
      }
      if (event.key === "ArrowUp") {
        selectedIndex = count ? (selectedIndex - 1 + count) % count : 0;
        draw();
        return true;
      }
      if (event.key === "Enter") {
        const item = current.items[selectedIndex];
        if (item) current.command(item);
        return true;
      }
      if (event.key === "Escape") {
        return true;
      }
      return false;
    },
    onExit() {
      root?.unmount();
      container?.remove();
      root = null;
      container = null;
      current = null;
    },
  };
}
