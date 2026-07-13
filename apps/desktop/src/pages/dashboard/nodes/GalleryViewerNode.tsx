import { useState, useMemo, useRef } from "react";
import { NodeResizer, type NodeProps } from "@xyflow/react";
import type { GalleryViewerNode, GalleryImage } from "./node-types";
import { AuthImage } from "../../../components/AuthImage";

type Cols = 2 | 3 | 4;
type Source = "all" | "chat" | "diary";

const MAX_IMAGES = 80;
const MAX_CELL_PX = 160;
const MAX_COLS = 4;
const CELL_GAP_PX = 1;
const NODE_BORDER_PX = 2;
const MAX_NODE_WIDTH =
  MAX_COLS * MAX_CELL_PX + (MAX_COLS - 1) * CELL_GAP_PX + NODE_BORDER_PX;

function matchesQuery(img: GalleryImage, q: string): boolean {
  if (!q) return true;
  const lower = q.toLowerCase();
  return (
    (img.filename ?? "").toLowerCase().includes(lower) ||
    (img.caption ?? "").toLowerCase().includes(lower)
  );
}

function SearchIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
      <circle cx="4" cy="4" r="2.5" stroke="currentColor" strokeWidth="1.3" />
      <line x1="6.4" y1="6.4" x2="9" y2="9" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

export function GalleryViewerNode({ data }: NodeProps<GalleryViewerNode>) {
  const { images, onNavigate, onImageClick, onClose } = data;

  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [cols, setCols] = useState<Cols>(3);
  const [source, setSource] = useState<Source>("all");
  const searchRef = useRef<HTMLInputElement>(null);

  const hasBothSources =
    images.some((i) => i.source === "chat") &&
    images.some((i) => i.source === "diary");

  const filtered = useMemo(
    () =>
      images
        .filter(
          (img) =>
            (source === "all" || img.source === source) &&
            matchesQuery(img, query),
        )
        .slice(0, MAX_IMAGES),
    [images, source, query],
  );

  const toggleSearch = () => {
    const next = !searchOpen;
    setSearchOpen(next);
    if (!next) setQuery("");
    else setTimeout(() => searchRef.current?.focus(), 40);
  };

  const cycleSource = () => {
    const order: Source[] = ["all", "chat", "diary"];
    setSource(order[(order.indexOf(source) + 1) % order.length]);
  };

  const isFiltered = query.length > 0 || source !== "all";

  return (
    <>
      <NodeResizer
        minWidth={220}
        minHeight={180}
        maxWidth={MAX_NODE_WIDTH}
        maxHeight={900}
        lineStyle={{ borderColor: "var(--border)", borderWidth: 1, opacity: 0.35 }}
        handleStyle={{
          width: 8,
          height: 8,
          borderRadius: 2,
          border: "1px solid var(--border)",
          background: "var(--background)",
          opacity: 0.55,
        }}
      />

      {/* Outer wrapper — overflow-visible so close button can bleed above */}
      <div className="group/node relative w-full h-full overflow-visible">

        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute -top-5 right-0 z-20 h-4 px-1.5 flex items-center rounded-sm bg-background/60 border border-foreground/[0.07] font-mono text-[8px] text-foreground/25 hover:text-foreground/60 hover:bg-background/80 opacity-0 group-hover/node:opacity-100 transition-all duration-200 backdrop-blur-sm"
          aria-label="Close gallery"
        >
          ×
        </button>

        {/* Glass card */}
        <div className="w-full h-full overflow-hidden rounded-xl bg-background/25 backdrop-blur-[36px] border border-foreground/[0.08] shadow-[0_4px_28px_rgba(0,0,0,0.2)] flex flex-col">

          {/* ── Header ── */}
          <div className="shrink-0 flex items-center gap-1.5 px-3 h-9 border-b border-foreground/[0.06]">
            <span className="font-mono text-[8px] tracking-[0.3em] uppercase text-foreground/30 mr-auto">
              Gallery
            </span>

            {/* Image count */}
            <span className="font-mono text-[8px] text-foreground/20">
              {isFiltered ? `${filtered.length}/${images.length}` : images.length}
            </span>

            <span className="w-px h-3 bg-foreground/[0.07] mx-0.5" />

            {/* Column picker — plain numbers, much clearer than dot grids */}
            <div className="flex items-center gap-px">
              {([2, 3, 4] as Cols[]).map((c) => (
                <button
                  key={c}
                  onClick={() => setCols(c)}
                  className={[
                    "font-mono text-[8px] w-5 h-5 flex items-center justify-center rounded transition-all duration-100",
                    cols === c
                      ? "text-foreground/70 bg-foreground/[0.09]"
                      : "text-foreground/20 hover:text-foreground/50",
                  ].join(" ")}
                >
                  {c}
                </button>
              ))}
            </div>

            {/* Source cycle — only if both exist */}
            {hasBothSources && (
              <>
                <span className="w-px h-3 bg-foreground/[0.07] mx-0.5" />
                <button
                  onClick={cycleSource}
                  className={[
                    "font-mono text-[7px] tracking-[0.14em] uppercase px-1.5 h-4 rounded transition-all duration-100",
                    source !== "all"
                      ? "bg-accent/[0.12] text-accent/70 border border-accent/20"
                      : "text-foreground/20 hover:text-foreground/50",
                  ].join(" ")}
                  title={`Filter by source (current: ${source})`}
                >
                  {source}
                </button>
              </>
            )}

            <span className="w-px h-3 bg-foreground/[0.07] mx-0.5" />

            {/* Search toggle */}
            <button
              onClick={toggleSearch}
              className={[
                "w-5 h-5 flex items-center justify-center rounded transition-all duration-100",
                searchOpen || query
                  ? "text-foreground/60 bg-foreground/[0.09]"
                  : "text-foreground/20 hover:text-foreground/50",
              ].join(" ")}
              aria-label="Search gallery"
            >
              <SearchIcon />
            </button>
          </div>

          {/* ── Search bar (collapsible) ── */}
          {searchOpen && (
            <div className="shrink-0 flex items-center gap-2 px-3 h-8 border-b border-foreground/[0.06] bg-foreground/[0.025]">
              <span className="text-foreground/20 shrink-0"><SearchIcon /></span>
              <input
                ref={searchRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="search by filename or caption…"
                className="flex-1 min-w-0 bg-transparent font-mono text-[9px] tracking-wide text-foreground/60 placeholder:text-foreground/20 focus:outline-none"
              />
              {query && (
                <button
                  onClick={() => setQuery("")}
                  className="font-mono text-[10px] text-foreground/20 hover:text-foreground/50 transition-colors shrink-0"
                >
                  ×
                </button>
              )}
            </div>
          )}

          {/* ── Grid ── */}
          {filtered.length === 0 ? (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center space-y-2 px-6">
                <p className="font-mono text-[8px] tracking-[0.24em] uppercase text-foreground/20">
                  {query
                    ? "No matches"
                    : source !== "all"
                      ? `No ${source} images`
                      : "No images yet"}
                </p>
                {!query && source === "all" && (
                  <p className="font-mono text-[8px] text-foreground/12 leading-relaxed">
                    share photos in chat to see them here
                  </p>
                )}
              </div>
            </div>
          ) : (
            <div
              className="flex-1 min-h-0 overflow-y-auto nowheel"
              style={{ scrollbarWidth: "none" }}
            >
              <div
                className="grid"
                style={{
                  gridTemplateColumns: `repeat(${cols}, minmax(0, ${MAX_CELL_PX}px))`,
                  width: `min(100%, ${cols * MAX_CELL_PX + (cols - 1) * CELL_GAP_PX}px)`,
                  gap: 1,
                  backgroundColor: "color-mix(in srgb, var(--border) 45%, transparent)",
                }}
              >
                {filtered.map((img, i) => (
                  <button
                    key={img.id}
                    onClick={() => onImageClick(filtered, i)}
                    className="relative aspect-square overflow-hidden bg-card/40 group/thumb"
                  >
                    <AuthImage
                      src={img.url}
                      alt={img.caption ?? img.filename ?? ""}
                      className="w-full h-full object-cover transition-transform duration-300 group-hover/thumb:scale-[1.06]"
                    />

                    {/* Hover overlay */}
                    <div className="absolute inset-0 bg-black/0 group-hover/thumb:bg-black/25 transition-colors duration-200" />

                    {/* Source badge */}
                    <span
                      className={[
                        "absolute top-1.5 right-1.5 font-mono text-[6px] tracking-wider uppercase px-1 py-px rounded-sm leading-tight",
                        "opacity-0 group-hover/thumb:opacity-100 transition-opacity duration-150",
                        img.source === "chat"
                          ? "bg-sky-500/25 text-sky-200/90"
                          : "bg-violet-500/25 text-violet-200/90",
                      ].join(" ")}
                    >
                      {img.source}
                    </span>

                    {/* Caption / filename */}
                    {(img.caption ?? img.filename) && (
                      <div className="absolute bottom-0 left-0 right-0 px-1.5 pt-4 pb-1 bg-gradient-to-t from-black/55 to-transparent opacity-0 group-hover/thumb:opacity-100 transition-opacity duration-200">
                        <span className="font-mono text-[6.5px] leading-tight text-white/80 line-clamp-1 block">
                          {img.caption ?? img.filename}
                        </span>
                      </div>
                    )}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Footer */}
          {images.length > 0 && (
            <div className="shrink-0 px-3 h-7 border-t border-foreground/[0.05] flex items-center justify-between">
              <span className="font-mono text-[7px] tracking-wider text-foreground/18">
                {images.length > MAX_IMAGES ? `showing ${MAX_IMAGES} of ${images.length}` : `${images.length} image${images.length === 1 ? "" : "s"}`}
              </span>
              <button
                onClick={() => onNavigate("/memory/images")}
                className="font-mono text-[7px] tracking-wider text-foreground/25 hover:text-foreground/55 transition-colors"
              >
                open
              </button>
            </div>
          )}

        </div>
      </div>
    </>
  );
}
