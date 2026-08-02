// Pure search/filter predicate over diary entries. Extracted (Task 12) from
// the `filteredEntries` useMemo that used to live directly in
// DiaryWorkspace.tsx, unchanged in behavior — moved so it is independently
// unit-testable (see tests/diary-entry-filter.test.ts) and so
// LibrarySidebar can own its own mood/date filter UI state without the
// parent re-implementing the matching logic.
import type { DiaryEntryData } from "@anima/api-client";
import { plainTextOfBody } from "./textFormat";

export interface DiaryEntryFilterCriteria {
  query: string;
  activeFolderId: number | null;
  moodFilter: string;
  dateFrom: string;
  dateTo: string;
}

export function filterDiaryEntries(
  entries: DiaryEntryData[],
  criteria: DiaryEntryFilterCriteria,
): DiaryEntryData[] {
  const query = criteria.query.trim().toLowerCase();
  return entries.filter((entry) => {
    if (criteria.activeFolderId != null && entry.folderId !== criteria.activeFolderId) return false;
    if (criteria.moodFilter && entry.mood !== criteria.moodFilter) return false;
    if (criteria.dateFrom && entry.entryDate < criteria.dateFrom) return false;
    if (criteria.dateTo && entry.entryDate > criteria.dateTo) return false;
    if (!query) return true;
    const haystack = `${entry.title ?? ""} ${plainTextOfBody(entry.body)} ${entry.mood ?? ""}`.toLowerCase();
    return haystack.includes(query);
  });
}
