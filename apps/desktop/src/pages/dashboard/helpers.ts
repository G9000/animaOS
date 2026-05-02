import type { Thread } from "@anima/api-client";

export function getTimeOfDay(): string {
  const h = new Date().getHours();
  if (h < 5) return "night";
  if (h < 12) return "morning";
  if (h < 17) return "afternoon";
  if (h < 21) return "evening";
  return "night";
}

/** Format a due date for display — relative when close, absolute otherwise */
export function formatDueDate(iso: string): string {
  const due = new Date(iso);
  if (Number.isNaN(due.getTime())) return "";

  const now = new Date();
  const diffMs = due.getTime() - now.getTime();
  const diffMin = Math.round(diffMs / 60_000);
  const diffHrs = Math.round(diffMs / 3_600_000);

  // Past due
  if (diffMs < 0) {
    const ago = Math.abs(diffMin);
    if (ago < 60) return `${ago}m overdue`;
    const hrsAgo = Math.abs(diffHrs);
    if (hrsAgo < 24) return `${hrsAgo}h overdue`;
    return "overdue";
  }

  // Future
  if (diffMin < 60) return `in ${diffMin}m`;
  if (diffHrs < 24) return `in ${diffHrs}h`;

  // Show day name if within this week
  const diffDays = Math.ceil(diffMs / 86_400_000);
  if (diffDays <= 7) {
    const dayName = due.toLocaleDateString("en-US", { weekday: "short" });
    const time = due.toLocaleTimeString("en-US", {
      hour: "numeric",
      minute: "2-digit",
    });
    return `${dayName} ${time}`;
  }

  // Farther out — show date
  return due.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export const PRIORITY_INDICATOR: Record<number, { dot: string; label: string }> = {
  0: { dot: "", label: "" },
  1: { dot: "bg-amber-400", label: "High" },
  2: { dot: "bg-red-500", label: "Urgent" },
};

/** Get initials from a name */
export function getInitials(name: string): string {
  return name
    .split(" ")
    .map((w) => w[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

/** Format thread timestamp nicely */
export function formatThreadTimestamp(thread: Thread): string {
  const raw = thread.lastMessageAt ?? thread.createdAt;
  if (!raw) return "";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return "";

  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.round(diffMs / 60_000);
  const diffHrs = Math.round(diffMs / 3_600_000);
  const diffDays = Math.round(diffMs / 86_400_000);

  if (diffMin < 1) return "now";
  if (diffMin < 60) return `${diffMin}m`;
  if (diffHrs < 24) return `${diffHrs}h`;
  if (diffDays < 7) return `${diffDays}d`;

  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}
