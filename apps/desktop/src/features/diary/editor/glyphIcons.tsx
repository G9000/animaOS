import { cn } from "@anima/standard-templates";

// Shared with DiaryWorkspace.tsx, which still uses `Glyph` for its own
// (non-slash-menu) icons — kept here as the single source rather than
// duplicated in both places.
export function Glyph({ children, className }: { children: React.ReactNode; className?: string }) {
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

export function ParagraphGlyphIcon() {
  return (
    <Glyph>
      <path d="M4 6h16M4 12h16M4 18h10" />
    </Glyph>
  );
}

export function HeadingGlyphIcon({ level }: { level: 1 | 2 | 3 }) {
  return (
    <Glyph>
      <path d="M5 5v14M13 5v14M5 12h8" />
      <text x="15" y="18" fontSize="8.5" fontFamily="ui-monospace, monospace" fill="currentColor" stroke="none">
        {level}
      </text>
    </Glyph>
  );
}

export function BulletListGlyphIcon() {
  return (
    <Glyph>
      <circle cx="5" cy="6" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="5" cy="12" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="5" cy="18" r="1.1" fill="currentColor" stroke="none" />
      <path d="M9.5 6h10M9.5 12h10M9.5 18h10" />
    </Glyph>
  );
}

export function OrderedListGlyphIcon() {
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

export function QuoteGlyphIcon() {
  return (
    <Glyph>
      <path d="M6 8.5c-1.4 0-2.5 1.2-2.5 3.5S4.6 15.5 6 15.5M14 8.5c-1.4 0-2.5 1.2-2.5 3.5s1.1 3.5 2.5 3.5" />
    </Glyph>
  );
}

export function CodeGlyphIcon() {
  return (
    <Glyph>
      <path d="M9 7L4 12l5 5M15 7l5 5-5 5" />
    </Glyph>
  );
}

export function DividerGlyphIcon() {
  return (
    <Glyph>
      <path d="M5 12h14" />
    </Glyph>
  );
}

// New block types the Tiptap extension factory registers (task lists,
// tables, toggles) — no prior visual reference, so these are drawn to match
// the existing glyph language (stroke-only, size-4, square joins).
export function TaskListGlyphIcon() {
  return (
    <Glyph>
      <rect x="3.5" y="4.5" width="4" height="4" rx="0" />
      <path d="M4.3 6.5l0.9 0.9 1.8-1.8" />
      <rect x="3.5" y="15.5" width="4" height="4" rx="0" />
      <path d="M9.5 6.5h11M9.5 17.5h11" />
    </Glyph>
  );
}

export function TableGlyphIcon() {
  return (
    <Glyph>
      <rect x="4" y="5" width="16" height="14" rx="0" />
      <path d="M4 10h16M10 5v14" />
    </Glyph>
  );
}

export function ToggleGlyphIcon() {
  return (
    <Glyph>
      <path d="M8 9l4 4 4-4" />
      <path d="M4 18h16" />
    </Glyph>
  );
}

// Selection bubble menu glyphs (Task 6) — same stroke-only, size-4, square-join
// language as the slash menu icons above.
export function BoldGlyphIcon() {
  return (
    <Glyph>
      <path d="M7 5h6a3.5 3.5 0 0 1 0 7H7zM7 12h7a3.5 3.5 0 0 1 0 7H7z" />
    </Glyph>
  );
}

export function ItalicGlyphIcon() {
  return (
    <Glyph>
      <path d="M13 5h5M6 19h5M14.5 5L9.5 19" />
    </Glyph>
  );
}

export function StrikeGlyphIcon() {
  return (
    <Glyph>
      <path d="M4 12h16M7.5 7c0-1.7 2-3 4.5-3s4.5 1 4.5 2.6M8 17.4c0 1.6 2 2.9 4.5 2.9s4.5-1.3 4.5-3" />
    </Glyph>
  );
}

export function LinkGlyphIcon() {
  return (
    <Glyph>
      <path d="M9.5 14.5l5-5" />
      <path d="M11 6.5l1.4-1.4a3.5 3.5 0 0 1 5 5L16 11.5M13 17.5l-1.4 1.4a3.5 3.5 0 0 1-5-5L8 12.5" />
    </Glyph>
  );
}

// Block drag handle glyphs (Task 7) — same stroke-only, size-4, square-join
// language as the rest of this file's icons.
export function GripGlyphIcon() {
  return (
    <Glyph>
      <circle cx="9" cy="6" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="9" cy="12" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="9" cy="18" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="15" cy="6" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="15" cy="12" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="15" cy="18" r="1.2" fill="currentColor" stroke="none" />
    </Glyph>
  );
}

export function EllipsisGlyphIcon() {
  return (
    <Glyph>
      <circle cx="5.5" cy="12" r="1.3" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none" />
      <circle cx="18.5" cy="12" r="1.3" fill="currentColor" stroke="none" />
    </Glyph>
  );
}
