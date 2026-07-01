import type {
  DiaryEntryData,
  Thread,
  ThreadMessage,
} from "@anima/api-client";

export type MemoryImageSource = "chat" | "diary";

export interface MemoryImage {
  id: string;
  url: string;
  mimeType: string;
  filename: string | null;
  sizeBytes: number | null;
  caption: string | null;
  sha256?: string | null;
  createdAt: string | null;
  source: MemoryImageSource;
  threadId?: number;
  threadTitle?: string | null;
  messageId?: number;
  entryId?: number;
  assetId?: number | null;
  retentionState?: string | null;
  references: MemoryImageReference[];
}

export interface MemoryImageReference {
  id: string;
  source: MemoryImageSource;
  mimeType?: string | null;
  createdAt: string | null;
  filename: string | null;
  sizeBytes: number | null;
  caption: string | null;
  sha256?: string | null;
  threadId?: number;
  threadTitle?: string | null;
  messageId?: number;
  entryId?: number;
  assetId?: number | null;
  retentionState?: string | null;
}

export interface MemoryImageThreadGroup {
  thread: Pick<Thread, "id" | "title" | "createdAt" | "lastMessageAt">;
  messages: ThreadMessage[];
}

export interface BuildMemoryImagesInput {
  diaryEntries: DiaryEntryData[];
  threadGroups: MemoryImageThreadGroup[];
}

export interface MemoryImageFilter {
  query: string;
  source: MemoryImageSource | "all";
}

export type MemoryImageSourceTarget =
  | { path: "/chat"; state: { resumeThreadId: number } }
  | { path: "/journal"; state?: undefined };

function imageTimestamp(image: Pick<MemoryImage, "createdAt">): number {
  if (!image.createdAt) return 0;
  const timestamp = new Date(image.createdAt).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function normalizedImageUrl(url: string): string {
  return url.trim().split(/[?#]/, 1)[0];
}

function normalizedFilename(filename: string): string {
  return filename.trim().toLowerCase();
}

function imageKeys(image: MemoryImage): string[] {
  const keys: string[] = [];
  if (image.assetId != null) keys.push(`asset:${image.assetId}`);
  if (image.url) keys.push(`url:${normalizedImageUrl(image.url)}`);
  if (image.filename && image.sizeBytes != null && image.sizeBytes > 0) {
    keys.push(
      [
        "file",
        image.mimeType.trim().toLowerCase(),
        normalizedFilename(image.filename),
        image.sizeBytes,
      ].join(":"),
    );
  }
  if (image.filename && image.createdAt) {
    keys.push(`file:${image.filename}:${image.createdAt}`);
  }
  return keys.length > 0 ? keys : [`${image.source}:${image.id}`];
}

function imageReference(image: MemoryImage): MemoryImageReference {
  return {
    id: image.id,
    source: image.source,
    mimeType: image.mimeType,
    createdAt: image.createdAt,
    filename: image.filename,
    sizeBytes: image.sizeBytes,
    caption: image.caption,
    sha256: image.sha256 ?? null,
    threadId: image.threadId,
    threadTitle: image.threadTitle,
    messageId: image.messageId,
    entryId: image.entryId,
    assetId: image.assetId,
    retentionState: image.retentionState,
  };
}

function imageSources(image: MemoryImage): MemoryImageSource[] {
  return image.references.length > 0
    ? image.references.map((reference) => reference.source)
    : [image.source];
}

function matchesQuery(image: MemoryImage, query: string): boolean {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;

  const directValues = [
    image.filename,
    image.caption,
    image.threadTitle,
    image.source,
  ];
  const referenceValues = image.references.flatMap((reference) => [
    reference.filename,
    reference.caption,
    reference.threadTitle,
    reference.source,
  ]);

  return [...directValues, ...referenceValues].some((value) =>
    (value ?? "").toLowerCase().includes(normalized),
  );
}

export function sortMemoryImages(images: MemoryImage[]): MemoryImage[] {
  return [...images].sort((a, b) => imageTimestamp(b) - imageTimestamp(a));
}

export function dedupeMemoryImages(images: MemoryImage[]): MemoryImage[] {
  const byKey = new Map<string, MemoryImage>();

  images.forEach((image) => {
    const keys = imageKeys(image);
    const reference = imageReference(image);
    const existing = keys.map((key) => byKey.get(key)).find(Boolean);

    if (!existing) {
      const nextImage = {
        ...image,
        references: image.references.length > 0 ? image.references : [reference],
      };
      keys.forEach((key) => byKey.set(key, nextImage));
      return;
    }

    existing.references.push(reference);
    keys.forEach((key) => byKey.set(key, existing));
  });

  return [...new Set(byKey.values())];
}

export function buildMemoryImages({
  diaryEntries,
  threadGroups,
}: BuildMemoryImagesInput): MemoryImage[] {
  const diaryImages: MemoryImage[] = diaryEntries
    .flatMap((entry) => entry.attachments)
      .filter((attachment) => attachment.kind === "image" || attachment.mimeType.startsWith("image/"))
      .map((attachment) => ({
        id: `diary:${attachment.id}`,
        url: attachment.url,
        mimeType: attachment.mimeType,
      filename: attachment.filename,
      sizeBytes: attachment.sizeBytes,
      caption: attachment.caption,
      createdAt: attachment.createdAt,
        source: "diary" as const,
        entryId: attachment.entryId,
        sha256: attachment.sha256,
        references: [],
      }));

  const chatImages: MemoryImage[] = threadGroups.flatMap(({ thread, messages }) =>
    messages
      .filter((message) => message.role === "user" && (message.attachments?.length ?? 0) > 0)
      .flatMap((message) =>
        (message.attachments ?? [])
          .filter((attachment) => attachment.kind === "image")
          .map((attachment) => ({
            id: `chat:${message.id ?? attachment.id}:${attachment.id}`,
            url: attachment.url,
            mimeType: attachment.mimeType,
            filename: attachment.filename ?? null,
            sizeBytes: attachment.sizeBytes ?? null,
            sha256: (attachment as { sha256?: string | null }).sha256 ?? null,
            caption: null,
            createdAt: message.ts ?? thread.lastMessageAt ?? thread.createdAt ?? null,
            source: "chat" as const,
            threadId: thread.id,
            threadTitle: thread.title,
            messageId: message.id ?? undefined,
            assetId: attachment.assetId ?? null,
            retentionState: attachment.retentionState ?? null,
            references: [],
          })),
      ),
  );

  return dedupeMemoryImages(sortMemoryImages([...chatImages, ...diaryImages]));
}

export function filterMemoryImages(
  images: MemoryImage[],
  filter: MemoryImageFilter,
): MemoryImage[] {
  return images.filter(
    (image) =>
      (filter.source === "all" || imageSources(image).includes(filter.source)) &&
      matchesQuery(image, filter.query),
  );
}

export function memoryImageSourceTarget(
  source: Pick<MemoryImageReference, "source" | "threadId">,
): MemoryImageSourceTarget {
  if (source.source === "chat" && source.threadId != null) {
    return { path: "/chat", state: { resumeThreadId: source.threadId } };
  }
  return { path: "/journal" };
}
