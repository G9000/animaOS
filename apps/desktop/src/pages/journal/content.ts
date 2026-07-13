export interface DiaryEditorSnapshot {
  editorIsEmpty: boolean;
  editorHtml: string;
  plainText: string;
}

export interface DiarySaveEligibility {
  editorHasContent: boolean;
  plainText: string;
  attachmentCount: number;
  hasPendingCover: boolean;
}

export function canSaveDiaryEntry(eligibility: DiarySaveEligibility): boolean {
  return (
    eligibility.editorHasContent ||
    eligibility.plainText.trim().length > 0 ||
    eligibility.attachmentCount > 0 ||
    eligibility.hasPendingCover
  );
}

export function resolveDiaryBody(snapshot: DiaryEditorSnapshot): string | null {
  if (!snapshot.editorIsEmpty) return snapshot.editorHtml;

  const fallback = snapshot.plainText.trim();
  return fallback || null;
}
