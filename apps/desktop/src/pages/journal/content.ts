export interface DiaryEditorSnapshot {
  editorIsEmpty: boolean;
  editorHtml: string;
  plainText: string;
}

export function resolveDiaryBody(snapshot: DiaryEditorSnapshot): string | null {
  if (!snapshot.editorIsEmpty) return snapshot.editorHtml;

  const fallback = snapshot.plainText.trim();
  return fallback || null;
}
