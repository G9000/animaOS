import { describe, expect, test } from "bun:test";
import { partitionDroppedFiles } from "../src/features/diary/lib/fileDrop";

function file(name: string, type: string): File {
  return new File(["x"], name, { type });
}

describe("partitionDroppedFiles (PR #139, Finding 4)", () => {
  test("splits a mixed drop into image and non-image subsets, keeping both", () => {
    const image = file("photo.png", "image/png");
    const pdf = file("notes.pdf", "application/pdf");

    const { imageFiles, nonImageFiles } = partitionDroppedFiles([image, pdf]);

    // BEFORE the fix, DiaryEditor's handleDrop computed only `imageFiles`
    // and never captured the non-image remainder at all — a mixed drop's
    // PDF (or any other non-image file) simply had nowhere to go once the
    // handler decided to stopPropagation() for the image. This test
    // targets that partitioning directly: a mixed input must produce BOTH
    // a non-empty imageFiles set AND a non-empty nonImageFiles set — if
    // the non-image file were silently dropped, nonImageFiles would be
    // empty here instead of containing it.
    expect(imageFiles).toEqual([image]);
    expect(nonImageFiles).toEqual([pdf]);
  });

  test("a pure image drop produces no non-image subset", () => {
    const image = file("photo.png", "image/png");
    const { imageFiles, nonImageFiles } = partitionDroppedFiles([image]);
    expect(imageFiles).toEqual([image]);
    expect(nonImageFiles).toEqual([]);
  });

  test("a pure non-image drop produces no image subset", () => {
    const pdf = file("notes.pdf", "application/pdf");
    const { imageFiles, nonImageFiles } = partitionDroppedFiles([pdf]);
    expect(imageFiles).toEqual([]);
    expect(nonImageFiles).toEqual([pdf]);
  });
});
