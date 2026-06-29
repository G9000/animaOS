import type { ChatAttachment, ChatMessage } from "@anima/api-client";

export type AttachmentRemovalScope =
  | { kind: "single_message"; messageId: number }
  | { kind: "all_messages" };

export function removeMatchingAttachmentsFromMessages(
  messages: ChatMessage[],
  scope: AttachmentRemovalScope,
  predicate: (attachment: ChatAttachment) => boolean,
): ChatMessage[] {
  return messages.map((message) => {
    if (scope.kind === "single_message" && message.id !== scope.messageId) {
      return message;
    }

    const attachments = message.attachments ?? [];
    if (attachments.length === 0) return message;

    const nextAttachments = attachments.filter(
      (attachment) => !predicate(attachment),
    );
    if (nextAttachments.length === attachments.length) return message;

    return {
      ...message,
      attachments: nextAttachments,
    };
  });
}
