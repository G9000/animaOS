// ── Types ────────────────────────────────────────────────────────────────────
export type {
  ChatMessage,
  MessageRole,
  Thread,
  TraceEvent,
  TraceEventType,
} from "./types";

// ── Components ───────────────────────────────────────────────────────────────
export { ChatBubble, type ChatBubbleProps } from "./ChatBubble";
export { CompactChatBubble, type CompactChatBubbleProps } from "./CompactChatBubble";
export { ChatAvatar, type ChatAvatarProps } from "./ChatAvatar";
export { CopyButton, type CopyButtonProps } from "./CopyButton";
export { TracePanel, type TracePanelProps } from "./TracePanel";
export { ChatInput, type ChatInputProps } from "./ChatInput";

// ── Icons ────────────────────────────────────────────────────────────────────
export {
  ThinkIcon,
  TranslateIcon,
  TraceIcon,
  CopyIcon,
  CheckIcon,
  UserIcon,
  LightbulbIcon,
  ChevronDownIcon,
  XIcon,
  type IconProps,
} from "./icons";

// ── Utilities ─────────────────────────────────────────────────────────────────
export {
  shouldGroupMessages,
  formatTimestamp,
  formatFullTimestamp,
  formatJson,
  serializeTraceAsJson,
  serializeTraceAsText,
} from "./utils";
