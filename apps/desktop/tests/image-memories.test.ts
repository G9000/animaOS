import { describe, expect, test } from "bun:test";

import {
  buildMemoryImages,
  filterMemoryImages,
  memoryImageSourceTarget,
} from "../src/lib/image-memories";
import type { DiaryEntryData, Thread, ThreadMessage } from "@anima/api-client";

const thread = (id: number, title: string | null): Thread => ({
  id,
  title,
  status: "closed",
  isArchived: false,
  lastMessageAt: "2026-07-01T08:30:00.000Z",
  createdAt: "2026-07-01T08:00:00.000Z",
});

const message = (
  id: number,
  ts: string,
  filename: string,
  url: string,
  assetId: number | null = id + 100,
  sizeBytes = 1200,
): ThreadMessage => ({
  id,
  role: "user",
  content: "remember this image",
  ts,
  isArchivedHistory: false,
  attachments: [
    {
      id: `att-${id}`,
      kind: "image",
      mimeType: "image/png",
      filename,
      sizeBytes,
      assetId,
      retentionState: "retained",
      url,
    },
  ],
});

describe("memory image helpers", () => {
  test("builds newest-first gallery images from diary entries and thread messages", () => {
    const diaryEntries: DiaryEntryData[] = [
      {
        id: 10,
        userId: 1,
        entryDate: "2026-06-30",
        title: "desk",
        body: "desk photo",
        mood: null,
        source: "manual",
        createdAt: "2026-06-30T09:00:00.000Z",
        updatedAt: null,
        attachments: [
          {
            id: 55,
            entryId: 10,
            kind: "image",
            mimeType: "image/jpeg",
            filename: "desk.jpg",
            caption: "desk setup",
            sizeBytes: 2048,
            sha256: "abc",
            createdAt: "2026-06-30T09:05:00.000Z",
            url: "/api/diary/10/attachments/55",
          },
        ],
      },
    ];

    const images = buildMemoryImages({
      diaryEntries,
      threadGroups: [
        {
          thread: thread(7, "Project board"),
          messages: [
            message(
              1,
              "2026-06-29T12:00:00.000Z",
              "old-board.png",
              "/api/images/101",
            ),
            message(
              2,
              "2026-07-01T08:20:00.000Z",
              "current-board.png",
              "/api/images/101",
            ),
          ],
        },
      ],
    });

    expect(images.map((img) => img.filename)).toEqual([
      "current-board.png",
      "desk.jpg",
    ]);
    expect(images[0]).toMatchObject({
      source: "chat",
      threadId: 7,
      messageId: 2,
      threadTitle: "Project board",
      assetId: 102,
      retentionState: "retained",
    });
    expect(images[1]).toMatchObject({
      source: "diary",
      entryId: 10,
      caption: "desk setup",
    });
  });

  test("filters by source and filename caption or thread title", () => {
    const images = buildMemoryImages({
      diaryEntries: [],
      threadGroups: [
        {
          thread: thread(3, "Receipt planning"),
          messages: [
            message(
              9,
              "2026-07-01T08:20:00.000Z",
              "invoice.png",
              "/api/images/209",
            ),
          ],
        },
      ],
    });

    expect(filterMemoryImages(images, { query: "receipt", source: "all" })).toHaveLength(1);
    expect(filterMemoryImages(images, { query: "invoice", source: "chat" })).toHaveLength(1);
    expect(filterMemoryImages(images, { query: "invoice", source: "diary" })).toHaveLength(0);
  });

  test("dedupes repeated chat assets while preserving image references", () => {
    const images = buildMemoryImages({
      diaryEntries: [],
      threadGroups: [
        {
          thread: thread(12, "Reference board"),
          messages: [
            message(
              11,
              "2026-07-01T08:10:00.000Z",
              "first-upload.png",
              "/api/images/first-url",
              900,
            ),
            message(
              12,
              "2026-07-01T08:25:00.000Z",
              "second-upload.png",
              "/api/images/second-url",
              900,
            ),
          ],
        },
      ],
    });

    expect(images).toHaveLength(1);
    expect(images[0]).toMatchObject({
      filename: "second-upload.png",
      url: "/api/images/second-url",
      assetId: 900,
    });
    expect(images[0].references).toHaveLength(2);
    expect(images[0].references.map((reference) => reference.messageId)).toEqual([12, 11]);
  });

  test("dedupes repeated chat images across threads by file signature when asset ids differ", () => {
    const images = buildMemoryImages({
      diaryEntries: [],
      threadGroups: [
        {
          thread: thread(21, "First chat"),
          messages: [
            message(
              21,
              "2026-07-01T08:10:00.000Z",
              "screenshot.png",
              "/api/chat/messages/21/attachments/a",
              null,
              4096,
            ),
          ],
        },
        {
          thread: thread(22, "Second chat"),
          messages: [
            message(
              22,
              "2026-07-01T08:30:00.000Z",
              "screenshot.png",
              "/api/chat/messages/22/attachments/b",
              null,
              4096,
            ),
          ],
        },
      ],
    });

    expect(images).toHaveLength(1);
    expect(images[0]).toMatchObject({
      filename: "screenshot.png",
      threadId: 22,
      threadTitle: "Second chat",
    });
    expect(images[0].references.map((reference) => reference.threadTitle)).toEqual([
      "Second chat",
      "First chat",
    ]);
  });

  test("builds navigation targets for each related chat source", () => {
    const images = buildMemoryImages({
      diaryEntries: [],
      threadGroups: [
        {
          thread: thread(31, "Earlier source"),
          messages: [
            message(
              31,
              "2026-07-01T08:10:00.000Z",
              "source.png",
              "/api/chat/messages/31/attachments/a",
              null,
              4096,
            ),
          ],
        },
        {
          thread: thread(32, "Latest source"),
          messages: [
            message(
              32,
              "2026-07-01T08:30:00.000Z",
              "source.png",
              "/api/chat/messages/32/attachments/b",
              null,
              4096,
            ),
          ],
        },
      ],
    });

    expect(images[0].references.map(memoryImageSourceTarget)).toEqual([
      { path: "/chat", state: { resumeThreadId: 32 } },
      { path: "/chat", state: { resumeThreadId: 31 } },
    ]);
  });
});
