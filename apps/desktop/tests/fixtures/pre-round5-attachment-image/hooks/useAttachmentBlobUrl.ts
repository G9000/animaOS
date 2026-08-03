// Test-only stub. The real hook (apps/desktop/src/features/diary/hooks/
// useAttachmentBlobUrl.ts) is an unrelated, untouched dependency of
// AttachmentImage.tsx — it only matters for rendering an already-"ready"
// image's downloaded blob, which is not what the round-5 regression test
// exercises (upload completion timing vs. NodeView unmount). This stub
// exists purely so the pre-fix AttachmentImage.tsx module (vendored here
// verbatim from git HEAD to test the REAL pre-fix code path) can be
// imported in isolation without pulling in `lib/api` and its network
// surface. It deliberately never resolves, since no test in this file
// exercises the "ready" download path.
export function useAttachmentBlobUrl(
  _attachment: { entryId: number; id: number } | null | undefined,
  _onError?: (message: string) => void,
  _retryToken = 0,
): string | null {
  return null;
}
