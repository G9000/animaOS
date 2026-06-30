import { describe, expect, test } from "bun:test";
import type { ChatMessage } from "@anima/api-client";
import { removeMatchingAttachmentsFromMessages } from "../src/pages/chat/attachmentState";

function message(
  id: number,
  attachments: NonNullable<ChatMessage["attachments"]>,
): ChatMessage {
  return {
    id,
    userId: 7,
    role: "user",
    content: "",
    attachments,
  };
}

describe("removeMatchingAttachmentsFromMessages", () => {
  test("removes a forgotten asset from every visible message", () => {
    const messages = [
      message(1, [
        {
          id: "first",
          kind: "image",
          mimeType: "image/png",
          assetId: 10,
          url: "/api/images/10",
        },
        {
          id: "other",
          kind: "image",
          mimeType: "image/png",
          assetId: 11,
          url: "/api/images/11",
        },
      ]),
      {
        ...message(2, [
          {
            id: "second",
            kind: "image",
            mimeType: "image/png",
            assetId: 10,
            url: "/api/images/10",
          },
        ]),
        pills: [
          { kind: "image_source", label: "first.png", ref: "first" },
          { kind: "image_source", label: "asset.png", ref: "image:10" },
          { kind: "document_source", label: "Plan", ref: 44 },
        ],
      },
    ];

    const next = removeMatchingAttachmentsFromMessages(
      messages,
      { kind: "all_messages" },
      (attachment) => attachment.assetId === 10,
    );

    expect(next[0].attachments?.map((attachment) => attachment.id)).toEqual([
      "other",
    ]);
    expect(next[1].attachments).toEqual([]);
    expect(next[1].pills).toEqual([
      { kind: "document_source", label: "Plan", ref: 44 },
    ]);
  });

  test("can scope removal to one message", () => {
    const messages = [
      message(1, [
        {
          id: "first",
          kind: "image",
          mimeType: "image/png",
          assetId: 10,
          url: "/api/images/10",
        },
      ]),
      message(2, [
        {
          id: "second",
          kind: "image",
          mimeType: "image/png",
          assetId: 10,
          url: "/api/images/10",
        },
      ]),
    ];

    const next = removeMatchingAttachmentsFromMessages(
      messages,
      { kind: "single_message", messageId: 1 },
      (attachment) => attachment.assetId === 10,
    );

    expect(next[0].attachments).toEqual([]);
    expect(next[1].attachments?.map((attachment) => attachment.id)).toEqual([
      "second",
    ]);
  });

  test("clears matching image source pills outside a single removed message", () => {
    const messages = [
      message(1, [
        {
          id: "first",
          kind: "image",
          mimeType: "image/png",
          assetId: 10,
          url: "/api/images/10",
        },
      ]),
      {
        ...message(2, []),
        role: "assistant",
        pills: [
          { kind: "image_source", label: "first.png", ref: "first" },
          { kind: "document_source", label: "Plan", ref: 44 },
        ],
      },
      message(3, [
        {
          id: "second",
          kind: "image",
          mimeType: "image/png",
          assetId: 10,
          url: "/api/images/10",
        },
      ]),
      {
        ...message(4, []),
        role: "assistant",
        pills: [{ kind: "image_source", label: "asset.png", ref: "image:10" }],
      },
    ];

    const next = removeMatchingAttachmentsFromMessages(
      messages,
      { kind: "single_message", messageId: 1 },
      (attachment) => attachment.id === "first",
    );

    expect(next[0].attachments).toEqual([]);
    expect(next[1].pills).toEqual([
      { kind: "document_source", label: "Plan", ref: 44 },
    ]);
    expect(next[2].attachments?.map((attachment) => attachment.id)).toEqual([
      "second",
    ]);
    expect(next[3].pills).toEqual([
      { kind: "image_source", label: "asset.png", ref: "image:10" },
    ]);
  });
});
