// Pure text/formatting helpers shared across the diary feature's panels and
// hooks. Extracted verbatim (Task 12) from DiaryWorkspace.tsx, where they
// used to live as free functions — no behavior changed, only relocated so
// multiple panels (LibrarySidebar, DetailsDrawer) and hooks
// (useDiaryEntries' search/filter) can share them without importing from
// DiaryWorkspace.tsx itself.
import type { DiaryEntryData } from "@anima/api-client";

export function todayISODate(): string {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

export function isHtmlBody(body: string): boolean {
  return /^\s*</.test(body);
}

export function plainTextOfBody(body: string): string {
  if (!isHtmlBody(body)) return body;
  return body
    .replace(/<\/(p|h[1-6]|li|blockquote|pre)>/g, " ")
    .replace(/<[^>]+>/g, "")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

export function escapeHtmlForEditor(text: string): string {
  const escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  const withBreaks = escaped.replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br>");
  return `<p>${withBreaks}</p>`;
}

export function formatEntryDate(dateStr: string): string {
  const date = new Date(`${dateStr}T00:00:00`);
  if (Number.isNaN(date.getTime())) return dateStr;
  return date.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: date.getFullYear() !== new Date().getFullYear() ? "numeric" : undefined,
  });
}

export function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function entryExcerpt(entry: DiaryEntryData): string {
  const text = plainTextOfBody(entry.body).replace(/\s+/g, " ").trim();
  return text.length > 90 ? `${text.slice(0, 90)}…` : text;
}

export function countWords(text: string): number {
  const trimmed = text.trim();
  return trimmed ? trimmed.split(/\s+/).length : 0;
}

export const MOOD_PILL_CLASSES = [
  "bg-chart-1/15 text-chart-1 border-chart-1/30",
  "bg-accent-2/15 text-accent-2 border-accent-2/30",
  "bg-chart-4/20 text-chart-4 border-chart-4/40",
  "bg-accent/15 text-accent border-accent/30",
  "bg-chart-3/25 text-chart-3 border-chart-3/40",
];

export function moodPillClass(mood: string): string {
  let hash = 0;
  for (let i = 0; i < mood.length; i += 1) {
    hash = (hash * 31 + mood.charCodeAt(i)) >>> 0;
  }
  return MOOD_PILL_CLASSES[hash % MOOD_PILL_CLASSES.length];
}

export function isPreviewableAttachment(kind: string): boolean {
  return kind === "image" || kind === "audio" || kind === "video";
}

export function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
