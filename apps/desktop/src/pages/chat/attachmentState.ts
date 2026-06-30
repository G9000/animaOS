import type { ChatAttachment, ChatMessage } from "@anima/api-client";

export type AttachmentRemovalScope =
  | { kind: "single_message"; messageId: number }
  | { kind: "all_messages" };

export type ImageAttachmentDeleteResult = {
  imageAssetId: number | null;
  assetDeleted: boolean;
};

type ChatPill = NonNullable<ChatMessage["pills"]>[number];

export function removeImageAttachmentAfterDelete(
  messages: ChatMessage[],
  options: {
    messageId: number;
    attachment: ChatAttachment;
    result: ImageAttachmentDeleteResult;
  },
): ChatMessage[] {
  const removedAssetId = options.result.assetDeleted
    ? options.result.imageAssetId ?? options.attachment.assetId ?? null
    : null;
  if (removedAssetId != null) {
    return removeMatchingAttachmentsFromMessages(
      messages,
      { kind: "all_messages" },
      (attachment) => attachment.assetId === removedAssetId,
    );
  }

  return removeMatchingAttachmentsFromMessages(
    messages,
    { kind: "single_message", messageId: options.messageId },
    (attachment) => attachment.id === options.attachment.id,
  );
}

export function removeMatchingAttachmentsFromMessages(
  messages: ChatMessage[],
  scope: AttachmentRemovalScope,
  predicate: (attachment: ChatAttachment) => boolean,
): ChatMessage[] {
  const removedAttachmentIds = new Set<string>();
  const removedAssetIds = new Set<number>();

  for (const message of messages) {
    if (!isMessageInRemovalScope(message, scope)) continue;
    for (const attachment of message.attachments ?? []) {
      if (!predicate(attachment)) continue;
      removedAttachmentIds.add(attachment.id);
      if (attachment.assetId != null) {
        removedAssetIds.add(attachment.assetId);
      }
    }
  }

  return messages.map((message) => {
    const isInScope = isMessageInRemovalScope(message, scope);

    const attachments = message.attachments ?? [];
    const nextAttachments = isInScope
      ? attachments.filter((attachment) => !predicate(attachment))
      : attachments;
    const pills = message.pills ?? [];
    const canMatchAssetRefs = isInScope || scope.kind === "all_messages";
    const nextPills = pills.filter(
      (pill) =>
        !isMatchingImageSourcePill(
          pill,
          removedAttachmentIds,
          canMatchAssetRefs ? removedAssetIds : EMPTY_ASSET_IDS,
        ),
    );

    if (
      nextAttachments.length === attachments.length &&
      nextPills.length === pills.length
    ) {
      return message;
    }

    return {
      ...message,
      attachments: nextAttachments,
      pills: nextPills,
    };
  });
}

const EMPTY_ASSET_IDS = new Set<number>();

function isMessageInRemovalScope(
  message: ChatMessage,
  scope: AttachmentRemovalScope,
): boolean {
  return scope.kind === "all_messages" || message.id === scope.messageId;
}

function isMatchingImageSourcePill(
  pill: ChatPill,
  attachmentIds: Set<string>,
  assetIds: Set<number>,
): boolean {
  if (pill.kind !== "image_source") return false;
  const ref = pill.ref;
  if (typeof ref === "string") {
    if (attachmentIds.has(ref)) return true;
    if (ref.startsWith("image:")) {
      const assetId = Number.parseInt(ref.slice("image:".length), 10);
      return assetIds.has(assetId);
    }
  }
  return typeof ref === "number" && assetIds.has(ref);
}
