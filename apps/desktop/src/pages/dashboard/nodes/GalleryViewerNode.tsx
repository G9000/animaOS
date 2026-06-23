import { useState, useMemo } from "react";
import { NodeResizer, type NodeProps } from "@xyflow/react";
import type { GalleryViewerNode, GalleryImage } from "./node-types";
import { AuthImage } from "../../../components/AuthImage";

type Cols = 2 | 3 | 4;
type Source = "all" | "chat" | "diary";

const MAX_IMAGES = 80;
const MAX_CELL_PX = 160;           // tallest/widest a single thumbnail can be
const MAX_COLS = 4;                // widest column setting available
const CELL_GAP_PX = 1;            // gap between cells (matches style gap:1)
const NODE_BORDER_PX = 2;         // 1px border each side
// Maximum node width at which every column is fully saturated
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

function GridIcon({ cols, active }: { cols: Cols; active: boolean }) {
  const dots = cols * 2;
  return (
    <span
      className={`inline-grid gap-[2px] transition-opacity ${active ? "opacity-70" : "opacity-20 group-hover:opacity-40"}`}
      style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}
    >
      {Array.from({ length: dots }).map((_, i) => (
        <span key={i} className="block rounded-[1px] bg-current" style={{ width: 3, height: 3 }} />
      ))}
    </span>
  );
}

export function GalleryViewerNode({ data }: NodeProps<GalleryViewerNode>) {
  const { images, onNavigate, onImageClick, onClose } = data;

  const [query, setQuery] = useState("");
  const [cols, setCols] = useState<Cols>(3);
  const [source, setSource] = useState<Source>("all");

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

  return (
    <>
      <NodeResizer
        minWidth={220}
        minHeight={180}
        maxWidth={MAX_NODE_WIDTH}
        maxHeight={900}
        lineStyle={{ borderColor: "var(--border)", borderWidth: 1, opacity: 0.5 }}
        handleStyle={{
          width: 9,
          height: 9,
          borderRadius: 3,
          border: "1px solid var(--border)",
          background: "var(--card)",
          opacity: 0.7,
        }}
      />

      {/* Outer — overflow-visible so the floating close button can bleed above */}
      <div className="group relative w-full h-full bg-card border border-border/50 shadow-md rounded-xl overflow-visible">

        {/* Floating close */}
        <button
          onClick={onClose}
          className="absolute -top-7 right-1 z-20 w-5 h-5 flex items-center justify-center rounded-full bg-card/90 border border-border/50 text-muted-foreground/40 hover:text-foreground hover:bg-card opacity-0 group-hover:opacity-100 transition-all duration-150"
          aria-label="Close widget"
        >
          <span className="text-xs leading-none">×</span>
        </button>

        {/* Inner — clips to card shape, fills full height as flex column */}
        <div className="overflow-hidden rounded-xl h-full flex flex-col">

          {/* ── Header ── */}
          <div className="shrink-0 px-3 py-2 flex items-center justify-between border-b border-border/30 bg-card">
            <span className="font-mono text-[9px] tracking-[0.22em] uppercase text-muted-foreground/55">
              {query || source !== "all"
                ? `Gallery · ${filtered.length} / ${images.length}`
                : `Gallery · ${images.length}`}
            </span>
            <button
              onClick={() => onNavigate("/journal")}
              className="font-mono text-[8px] tracking-wider uppercase text-muted-foreground/35 hover:text-foreground/60 transition-colors"
            >
              Journal →
            </button>
          </div>

          {/* ── Toolbar ── */}
          <div className="shrink-0 flex items-center gap-2 px-3 pt-2.5 pb-2">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="filter…"
              className="flex-1 min-w-0 bg-transparent border-0 border-b border-border/30 font-mono text-[9px] tracking-wider text-foreground/70 placeholder:text-muted-foreground/20 focus:outline-none focus:border-border/60 pb-0.5 transition-colors"
            />
            <div className="flex items-center gap-2 shrink-0">
              {([2, 3, 4] as Cols[]).map((c) => (
                <button
                  key={c}
                  onClick={() => setCols(c)}
                  className="group/col leading-none"
                  aria-label={`${c} columns`}
                >
                  <GridIcon cols={c} active={cols === c} />
                </button>
              ))}
            </div>
          </div>

          {/* ── Source filter ── */}
          {hasBothSources && (
            <div className="shrink-0 flex items-center gap-3 px-3 pb-2">
              {(["all", "chat", "diary"] as Source[]).map((s) => (
                <button
                  key={s}
                  onClick={() => setSource(s)}
                  className={`flex items-center gap-1 font-mono text-[8px] tracking-[0.18em] uppercase transition-colors ${
                    source === s
                      ? "text-foreground/60"
                      : "text-muted-foreground/25 hover:text-muted-foreground/50"
                  }`}
                >
                  <span className={`w-1 h-1 rounded-full ${source === s ? "bg-foreground/60" : "bg-current"}`} />
                  {s}
                </button>
              ))}
            </div>
          )}

          {/* ── Grid — fills remaining height, scrollable ── */}
          {filtered.length === 0 ? (
            <div className="flex-1 flex items-center justify-center px-4 pb-4">
              <div className="text-center space-y-1">
                <p className="font-mono text-[9px] tracking-[0.18em] uppercase text-muted-foreground/25">
                  {query || source !== "all" ? "No matches" : "No images yet"}
                </p>
                {!query && source === "all" && (
                  <p className="font-mono text-[8px] text-muted-foreground/20 leading-relaxed">
                    share images in chat or attach them to journal entries
                  </p>
                )}
              </div>
            </div>
          ) : (
            <div
              className="flex-1 min-h-0 overflow-y-auto nowheel border-t border-border/20"
              style={{ scrollbarWidth: "none" }}
            >
              <div
                className="grid"
                style={{
                  gridTemplateColumns: `repeat(${cols}, minmax(0, ${MAX_CELL_PX}px))`,
                  width: `min(100%, ${cols * MAX_CELL_PX + (cols - 1) * CELL_GAP_PX}px)`,
                  gap: 1,
                  backgroundColor: "color-mix(in srgb, var(--border) 60%, transparent)",
                }}
              >
                {filtered.map((img, i) => (
                  <button
                    key={img.id}
                    onClick={() => onImageClick(filtered, i)}
                    className="relative aspect-square overflow-hidden bg-card group/thumb"
                  >
                    <AuthImage
                      src={img.url}
                      alt={img.caption ?? img.filename ?? ""}
                      className="w-full h-full object-cover transition-opacity duration-150 group-hover/thumb:opacity-85"
                    />
                    <span className="absolute bottom-0.5 left-0.5 font-mono text-[6px] tracking-wider text-white/70 bg-black/50 px-0.5 leading-tight opacity-0 group-hover/thumb:opacity-100 transition-opacity duration-150">
                      {img.source}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* ── Footer — only shown when capped at MAX_IMAGES ── */}
          {images.length > MAX_IMAGES && (
            <div className="shrink-0 px-3 py-1.5 border-t border-border/15 flex items-center justify-between">
              <span className="font-mono text-[8px] tracking-wider text-muted-foreground/25">
                showing {MAX_IMAGES} of {images.length}
              </span>
              <button
                onClick={() => onNavigate("/journal")}
                className="font-mono text-[8px] tracking-wider text-muted-foreground/30 hover:text-muted-foreground/60 transition-colors"
              >
                see all →
              </button>
            </div>
          )}

        </div>
      </div>
    </>
  );
}
