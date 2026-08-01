import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowRightIcon,
  ImageIcon,
  XIcon,
  cn,
} from "@anima/standard-templates";
import { useAuth } from "../../context/AuthContext";
import { AuthImage } from "../../components/AuthImage";
import { api } from "../../lib/api";
import {
  buildMemoryImages,
  filterMemoryImages,
  memoryImageSourceTarget,
  type MemoryImage,
  type MemoryImageReference,
  type MemoryImageSource,
} from "../../lib/image-memories";

type SourceFilter = MemoryImageSource | "all";
type Density = "large" | "medium" | "compact";

const DIARY_IMAGE_LIMIT = 200;
const THREAD_IMAGE_LIMIT = 80;

const SOURCE_FILTERS: SourceFilter[] = ["all", "chat", "diary"];
const DENSITIES: Density[] = ["large", "medium", "compact"];

function formatImageDate(value: string | null): string {
  if (!value) return "Unknown date";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown date";
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatByteSize(sizeBytes: number | null | undefined): string {
  if (sizeBytes == null || !Number.isFinite(sizeBytes) || sizeBytes < 0) return "Unknown";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = sizeBytes;
  let unitIndex = 0;

  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }

  if (unitIndex === 0) return `${value} ${units[unitIndex]}`;
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

function metadataString(reference: MemoryImageReference): string {
  const parts: string[] = [];
  if (reference.mimeType) parts.push(reference.mimeType);
  if (reference.sizeBytes != null) parts.push(formatByteSize(reference.sizeBytes));
  if (reference.sha256) parts.push(`sha256 ${reference.sha256}`);
  return parts.join(" • ");
}

function sourceLabel(image: MemoryImage): string {
  if (image.source === "chat") return image.threadTitle || "Chat";
  return image.caption || image.filename || "Diary";
}

function sourceReferenceLabel(reference: MemoryImageReference): string {
  if (reference.source === "chat") return reference.threadTitle || "Chat";
  return reference.caption || reference.filename || "Diary";
}

function sourceReferenceDetail(reference: MemoryImageReference): string {
  const parts = [reference.source, formatImageDate(reference.createdAt)];
  if (reference.filename) parts.push(reference.filename);
  if (reference.threadId != null && reference.source === "chat") {
    parts.push(`thread ${reference.threadId}`);
  }
  if (reference.entryId != null && reference.source === "diary") {
    parts.push(`entry ${reference.entryId}`);
  }
  return parts.join(" / ");
}

function imageReferenceCount(image: MemoryImage): number {
  return Math.max(1, image.references.length);
}

function imageHasSource(image: MemoryImage, source: MemoryImageSource): boolean {
  if (image.source === source) return true;
  return image.references.some((reference) => reference.source === source);
}

function imageTitle(image: MemoryImage): string {
  return image.caption || image.filename || sourceLabel(image);
}

function gridClass(density: Density): string {
  if (density === "compact") {
    return "grid-cols-3 sm:grid-cols-5 lg:grid-cols-7 2xl:grid-cols-9";
  }
  if (density === "medium") {
    return "grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-7";
  }
  return "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5";
}

function sourceCount(images: MemoryImage[], source: SourceFilter): number {
  if (source === "all") return images.length;
  return images.filter((image) => imageHasSource(image, source)).length;
}

interface ImageDetailProps {
  image: MemoryImage | null;
  forgetting: boolean;
  onClose: () => void;
  onOpenSource: (source: MemoryImage | MemoryImageReference) => void;
  onForget: (image: MemoryImage) => void;
}

function ImageDetail({
  image,
  forgetting,
  onClose,
  onOpenSource,
  onForget,
}: ImageDetailProps) {
  if (!image) {
    return (
      <div className="h-full flex items-center justify-center px-8 text-center">
        <div className="space-y-3">
          <div className="mx-auto size-12 border border-hairline bg-foreground/[0.03] grid place-items-center text-foreground/25">
            <ImageIcon size="sm" />
          </div>
          <p className="font-mono text-caption uppercase tracking-caps-4 text-foreground/35">
            Select an image
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full min-h-0 flex flex-col">
      <div className="shrink-0 flex items-center justify-between border-b border-hairline px-4 py-3">
        <span className="font-mono text-caption uppercase tracking-caps-4 text-foreground/40">
          Detail
        </span>
        <button
          type="button"
          onClick={onClose}
          className="size-8 grid place-items-center text-foreground/35 hover:text-foreground hover:bg-foreground/[0.06]"
          aria-label="Close image detail"
        >
          <XIcon size="sm" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 space-y-4">
        <div className="w-full overflow-hidden border border-hairline bg-foreground/[0.03]">
          <AuthImage
            src={image.url}
            alt={imageTitle(image)}
            className="max-h-[46vh] w-full object-contain"
          />
        </div>

        <div className="space-y-1">
          <h2 className="text-xl font-light tracking-[-0.02em] text-foreground break-words">
            {imageTitle(image)}
          </h2>
          <p className="font-mono text-caption uppercase tracking-caps-3 text-foreground/35">
            {image.source} / {formatImageDate(image.createdAt)}
          </p>
        </div>

        <div className="grid gap-2 font-mono text-caption text-foreground/45">
          {imageReferenceCount(image) > 1 && (
            <div className="flex items-center justify-between gap-4 border-b border-hairline-faint py-2">
              <span className="uppercase tracking-caps-3 text-foreground/25">Memories</span>
              <span className="text-right">{imageReferenceCount(image)}</span>
            </div>
          )}
          <div className="flex items-center justify-between gap-4 border-b border-hairline-faint py-2">
            <span className="uppercase tracking-caps-3 text-foreground/25">Source</span>
            <span className="text-right truncate">{sourceLabel(image)}</span>
          </div>
          {image.filename && (
            <div className="flex items-center justify-between gap-4 border-b border-hairline-faint py-2">
              <span className="uppercase tracking-caps-3 text-foreground/25">File</span>
              <span className="text-right truncate">{image.filename}</span>
            </div>
          )}
          {image.mimeType && (
            <div className="flex items-center justify-between gap-4 border-b border-hairline-faint py-2">
              <span className="uppercase tracking-caps-3 text-foreground/25">Type</span>
              <span className="text-right truncate">{image.mimeType}</span>
            </div>
          )}
          {image.sizeBytes != null && (
            <div className="flex items-center justify-between gap-4 border-b border-hairline-faint py-2">
              <span className="uppercase tracking-caps-3 text-foreground/25">Size</span>
              <span className="text-right">{formatByteSize(image.sizeBytes)}</span>
            </div>
          )}
          {image.threadId != null && (
            <div className="flex items-center justify-between gap-4 border-b border-hairline-faint py-2">
              <span className="uppercase tracking-caps-3 text-foreground/25">Thread</span>
              <span className="text-right">{image.threadId}</span>
            </div>
          )}
          {image.messageId != null && (
            <div className="flex items-center justify-between gap-4 border-b border-hairline-faint py-2">
              <span className="uppercase tracking-caps-3 text-foreground/25">Message</span>
              <span className="text-right">{image.messageId}</span>
            </div>
          )}
          {image.entryId != null && (
            <div className="flex items-center justify-between gap-4 border-b border-hairline-faint py-2">
              <span className="uppercase tracking-caps-3 text-foreground/25">Entry</span>
              <span className="text-right">{image.entryId}</span>
            </div>
          )}
          {image.retentionState && (
            <div className="flex items-center justify-between gap-4 border-b border-hairline-faint py-2">
              <span className="uppercase tracking-caps-3 text-foreground/25">State</span>
              <span className="text-right truncate">{image.retentionState}</span>
            </div>
          )}
          {image.sha256 && (
            <div className="flex items-center justify-between gap-4 border-b border-hairline-faint py-2">
              <span className="uppercase tracking-caps-3 text-foreground/25">Fingerprint</span>
              <span className="text-right font-mono text-label truncate">{image.sha256}</span>
            </div>
          )}
          {image.assetId != null && (
            <div className="flex items-center justify-between gap-4 border-b border-hairline-faint py-2">
              <span className="uppercase tracking-caps-3 text-foreground/25">Asset</span>
              <span className="text-right">{image.assetId}</span>
            </div>
          )}
        </div>

        <div className="flex flex-wrap gap-2 pt-1">
          <button
            type="button"
            onClick={() => onOpenSource(image)}
            className="inline-flex items-center gap-2 border border-hairline bg-foreground/[0.04] px-3 py-2 font-mono text-caption uppercase tracking-caps-3 text-foreground/60 hover:text-foreground hover:bg-foreground/[0.08]"
          >
            {imageReferenceCount(image) > 1 ? "Open latest source" : "Open source"}
            <ArrowRightIcon size="sm" />
          </button>
          {image.assetId != null && (
            <button
              type="button"
              disabled={forgetting}
              onClick={() => onForget(image)}
              className="border border-destructive/20 bg-destructive/5 px-3 py-2 font-mono text-caption uppercase tracking-caps-3 text-destructive/75 hover:text-destructive hover:bg-destructive/10 disabled:opacity-45"
            >
              {forgetting ? "Forgetting" : "Forget"}
            </button>
          )}
        </div>

        {image.references.length > 1 && (
          <div className="space-y-2 pt-1">
            <div className="flex items-center justify-between gap-3">
              <h3 className="font-mono text-caption uppercase tracking-caps-3 text-foreground/35">
                Related sources
              </h3>
              <span className="font-mono text-caption text-foreground/25">
                {image.references.length}
              </span>
            </div>
            <div className="grid gap-1.5">
              {image.references.map((reference, index) => (
                <button
                  key={`${reference.id}:${index}`}
                  type="button"
                  onClick={() => onOpenSource(reference)}
                  className="group flex min-w-0 items-center justify-between gap-3 border border-hairline bg-foreground/[0.025] px-3 py-2 text-left hover:border-hairline-strong hover:bg-foreground/[0.05]"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-detail text-foreground/70">
                      {sourceReferenceLabel(reference)}
                    </span>
                    <span className="mt-0.5 block truncate font-mono text-label uppercase tracking-caps-2 text-foreground/35">
                      {sourceReferenceDetail(reference)}
                    </span>
                    {metadataString(reference) && (
                      <span className="mt-0.5 block truncate font-mono text-micro text-foreground/30">
                        {metadataString(reference)}
                      </span>
                    )}
                  </span>
                  <ArrowRightIcon
                    size="sm"
                    className="shrink-0 text-foreground/25 group-hover:text-foreground/60"
                  />
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function MemoryImages() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [images, setImages] = useState<MemoryImage[]>([]);
  const [query, setQuery] = useState("");
  const [source, setSource] = useState<SourceFilter>("all");
  const [density, setDensity] = useState<Density>("medium");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forgettingAssetId, setForgettingAssetId] = useState<number | null>(null);

  useEffect(() => {
    if (user?.id == null) return;

    let active = true;
    setLoading(true);
    setError(null);

    void (async () => {
      try {
        const [diaryEntries, threadList] = await Promise.all([
          api.diary.list(user.id, DIARY_IMAGE_LIMIT),
          api.threads.list(),
        ]);
        if (!active) return;

        const threadGroups = await Promise.all(
          threadList.threads.slice(0, THREAD_IMAGE_LIMIT).map(async (thread) => ({
            thread,
            messages: await api.threads
              .messages(thread.id)
              .then((result) => result.messages)
              .catch(() => []),
          })),
        );
        if (!active) return;

        const nextImages = buildMemoryImages({ diaryEntries, threadGroups });
        setImages(nextImages);
        setSelectedId((current) => current ?? nextImages[0]?.id ?? null);
      } catch {
        if (active) setError("Failed to load image memories.");
      } finally {
        if (active) setLoading(false);
      }
    })();

    return () => {
      active = false;
    };
  }, [user?.id]);

  const filteredImages = useMemo(
    () => filterMemoryImages(images, { query, source }),
    [images, query, source],
  );

  const selectedImage = useMemo(() => {
    if (!selectedId) return null;
    return images.find((image) => image.id === selectedId) ?? null;
  }, [images, selectedId]);

  const openSource = useCallback(
    (source: MemoryImage | MemoryImageReference) => {
      const target = memoryImageSourceTarget(source);
      navigate(target.path, target.state ? { state: target.state } : undefined);
    },
    [navigate],
  );

  const forgetImage = useCallback(async (image: MemoryImage) => {
    if (image.assetId == null) return;
    if (!window.confirm("Forget this image everywhere?")) return;

    setForgettingAssetId(image.assetId);
    setError(null);
    try {
      await api.images.forget(image.assetId);
      setImages((current) =>
        current.filter((candidate) => candidate.assetId !== image.assetId),
      );
      setSelectedId(null);
    } catch {
      setError("Failed to forget image.");
    } finally {
      setForgettingAssetId(null);
    }
  }, []);

  return (
    <div className="h-full pt-hud overflow-hidden">
      <div className="flex h-full min-h-0 bg-background/10">
        <section className="min-w-0 flex-1 flex flex-col">
          <header className="shrink-0 border-b border-hairline bg-background/35 backdrop-blur-[32px] px-5 py-4">
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div className="space-y-1">
                <div className="flex items-center gap-2 font-mono text-caption uppercase tracking-caps-4 text-foreground/35">
                  <ImageIcon size="sm" />
                  Memory / Images
                </div>
                <h1 className="text-3xl font-light tracking-[-0.04em] text-foreground">
                  Visual Memory
                </h1>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search images"
                  className="h-9 w-48 border border-hairline bg-background/40 px-3 font-mono text-detail text-foreground outline-none placeholder:text-foreground/25 focus:border-accent/45"
                />

                <div className="flex border border-hairline bg-background/30">
                  {SOURCE_FILTERS.map((candidate) => (
                    <button
                      key={candidate}
                      type="button"
                      onClick={() => setSource(candidate)}
                      className={cn(
                        "h-9 px-3 font-mono text-caption uppercase tracking-caps-2 transition-colors",
                        source === candidate
                          ? "bg-accent text-accent-foreground"
                          : "text-foreground/40 hover:text-foreground hover:bg-foreground/[0.05]",
                      )}
                    >
                      {candidate}
                      <span className="ml-1 opacity-55">
                        {sourceCount(images, candidate)}
                      </span>
                    </button>
                  ))}
                </div>

                <div className="flex border border-hairline bg-background/30">
                  {DENSITIES.map((candidate) => (
                    <button
                      key={candidate}
                      type="button"
                      onClick={() => setDensity(candidate)}
                      className={cn(
                        "h-9 px-3 font-mono text-caption uppercase tracking-caps-2 transition-colors",
                        density === candidate
                          ? "bg-foreground text-background"
                          : "text-foreground/40 hover:text-foreground hover:bg-foreground/[0.05]",
                      )}
                    >
                      {candidate.slice(0, 1)}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </header>

          {error && (
            <div className="shrink-0 border-b border-destructive/20 bg-destructive/10 px-5 py-2 font-mono text-caption uppercase tracking-caps-3 text-destructive/80">
              {error}
            </div>
          )}

          <main className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
            {loading ? (
              <div className={cn("grid gap-2", gridClass(density))}>
                {Array.from({ length: 18 }).map((_, index) => (
                  <div
                    key={index}
                    className="aspect-square animate-pulse bg-foreground/[0.04] border border-hairline-faint"
                  />
                ))}
              </div>
            ) : filteredImages.length === 0 ? (
              <div className="h-full min-h-[320px] grid place-items-center">
                <div className="text-center space-y-3">
                  <div className="mx-auto size-12 border border-hairline bg-foreground/[0.03] grid place-items-center text-foreground/25">
                    <ImageIcon size="sm" />
                  </div>
                  <p className="font-mono text-caption uppercase tracking-caps-4 text-foreground/35">
                    No images
                  </p>
                </div>
              </div>
            ) : (
              <div className={cn("grid gap-2 pb-10", gridClass(density))}>
                {filteredImages.map((image) => {
                  const selected = image.id === selectedId;
                  return (
                    <button
                      key={image.id}
                      type="button"
                      onClick={() => setSelectedId(image.id)}
                      onDoubleClick={() => openSource(image)}
                      className={cn(
                        "group relative aspect-square overflow-hidden border bg-foreground/[0.03] text-left transition-colors",
                        selected
                          ? "border-accent"
                          : "border-hairline hover:border-hairline-strong",
                      )}
                    >
                      <AuthImage
                        src={image.url}
                        alt={imageTitle(image)}
                        className="absolute inset-0 h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.035]"
                      />
                      <div className="absolute inset-0 bg-gradient-to-t from-background/75 via-background/5 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
                      <div className="absolute left-2 top-2 flex gap-1">
                        <span
                          className={cn(
                            "px-1.5 py-0.5 font-mono text-micro uppercase tracking-caps-2 backdrop-blur",
                            image.source === "chat"
                              ? "bg-accent/85 text-accent-foreground"
                              : "bg-foreground/85 text-background",
                          )}
                        >
                          {image.source}
                        </span>
                      </div>
                      {imageReferenceCount(image) > 1 && (
                        <span className="absolute right-2 top-2 bg-background/85 px-1.5 py-0.5 font-mono text-micro uppercase tracking-caps-2 text-foreground/70 backdrop-blur">
                          {imageReferenceCount(image)} memories
                        </span>
                      )}
                      <div className="absolute inset-x-0 bottom-0 p-2 opacity-0 transition-opacity group-hover:opacity-100">
                        <p className="truncate font-mono text-caption text-foreground">
                          {imageTitle(image)}
                        </p>
                        <p className="mt-0.5 truncate font-mono text-micro uppercase tracking-caps-2 text-foreground/55">
                          {formatImageDate(image.createdAt)}
                        </p>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </main>
        </section>

        <aside className="hidden w-[360px] shrink-0 border-l border-hairline bg-background/30 backdrop-blur-[32px] xl:block">
          <ImageDetail
            image={selectedImage}
            forgetting={forgettingAssetId === selectedImage?.assetId}
            onClose={() => setSelectedId(null)}
            onOpenSource={openSource}
            onForget={forgetImage}
          />
        </aside>

        {selectedImage && (
          <div className="fixed inset-0 z-50 bg-background/90 backdrop-blur-sm xl:hidden">
            <ImageDetail
              image={selectedImage}
              forgetting={forgettingAssetId === selectedImage.assetId}
              onClose={() => setSelectedId(null)}
              onOpenSource={openSource}
              onForget={forgetImage}
            />
          </div>
        )}
      </div>
    </div>
  );
}
