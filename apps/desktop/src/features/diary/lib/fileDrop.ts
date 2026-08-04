// Finding 4 (PR #139): DiaryEditor's drop handler used to compute
// `imageFiles` inline and, whenever that set was non-empty, call
// `preventDefault()` + `stopPropagation()` and insert ONLY those files —
// silently dropping any non-image files in the same drop (e.g. an image
// plus a PDF dragged in together). The pure-non-image case already worked
// (the handler returns false and lets the event keep propagating up to
// the composer wrapper's own "Attach" drop handler); the gap was
// specifically a MIXED drop, where stopping propagation for the images
// also cut off the wrapper's chance to see the non-image files.
//
// Factored out as a pure function so the partitioning logic itself is
// unit-testable without needing a real DragEvent/DataTransfer or editor
// instance — see tests/diary-file-drop.test.ts.
export interface PartitionedDroppedFiles {
  imageFiles: File[];
  nonImageFiles: File[];
}

export function partitionDroppedFiles(files: Iterable<File>): PartitionedDroppedFiles {
  const imageFiles: File[] = [];
  const nonImageFiles: File[] = [];
  for (const file of files) {
    if (file.type.startsWith("image/")) {
      imageFiles.push(file);
    } else {
      nonImageFiles.push(file);
    }
  }
  return { imageFiles, nonImageFiles };
}
