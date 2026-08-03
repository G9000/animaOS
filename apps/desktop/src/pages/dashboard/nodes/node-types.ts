import type { Node } from "@xyflow/react";
import type {
  Greeting,
  Reflection,
  TaskItem,
  MemoryEpisodeData,
  EmotionalContextData,
  MessagePill,
  TodayContext,
  Nudge,
  Thread,
  DiaryEntryData,
} from "@anima/api-client";
import type { MemoryImage } from "../../../lib/image-memories";

export type GalleryImage = MemoryImage;

export type NodeType =
  | "profile"
  | "greeting"
  | "tasks"
  | "memory"
  | "mood"
  | "todayContext"
  | "nudge"
  | "reflection"
  | "gallery"
  | "recentChats"
  | "quickCapture"
  | "journal"
  | "systemMonitor"
  | "network";

export interface BaseNodeData extends Record<string, unknown> {
  type: NodeType;
  onClose: () => void;
}

export interface ProfileNodeData extends BaseNodeData {
  type: "profile";
  agentName: string;
  avatarUrl: string;
  relationship?: string | null;
  emotion?: string | null;
  mood: EmotionalContextData | null;
  lastSession?: string | null;
  brief?: Greeting | null;
  briefLoading?: boolean;
  todayContextLine?: string | null;
  currentFocus?: string | null;
  onExplore: (thought: string, pills?: MessagePill[]) => void;
  /** IL-015 (PR #135 review, P1): called from this node once it has
   * actually rendered a dream-bearing greeting. Acknowledging a dream marks
   * it surfaced FOREVER, so the signal has to come from a mounted surface
   * that really displayed the text — the dashboard lets the user close both
   * nodes that show it, and acking from the fetch handler consumed dreams
   * nothing on screen ever voiced. */
  onDreamShown?: () => void;
}

export interface GreetingNodeData extends BaseNodeData {
  type: "greeting";
  agentName: string;
  brief: Greeting | null;
  briefLoading: boolean;
  userName?: string;
  onChat: () => void;
  /** IL-015 (PR #135 review, P1): called from this node once it has
   * actually rendered a dream-bearing greeting. Acknowledging a dream marks
   * it surfaced FOREVER, so the signal has to come from a mounted surface
   * that really displayed the text — the dashboard lets the user close both
   * nodes that show it, and acking from the fetch handler consumed dreams
   * nothing on screen ever voiced. */
  onDreamShown?: () => void;
}

export interface TasksNodeData extends BaseNodeData {
  type: "tasks";
  tasks: TaskItem[];
  currentFocus?: string | null;
  onNavigate: (path: string) => void;
  onToggleTask: (task: TaskItem) => void;
  onDeleteTask: (id: number) => void;
  onAddTask: (text: string) => void;
}

export interface MemoryNodeData extends BaseNodeData {
  type: "memory";
  episodes: MemoryEpisodeData[];
  agentName: string;
  avatarUrl?: string;
  onChat: (episode: MemoryEpisodeData) => void;
  onRead: (episode: MemoryEpisodeData) => void;
  onViewAll: () => void;
}

export interface TodayContextNodeData extends BaseNodeData {
  type: "todayContext";
  context: TodayContext | null;
  greeting?: string | null;
  onSave: (context: TodayContext) => void;
  onClear: () => void;
}

export interface NudgeNodeData extends BaseNodeData {
  type: "nudge";
  nudges: Nudge[];
  onDismiss: (type: string) => void;
}

export type ProfileNode = Node<ProfileNodeData, "profile">;
export type GreetingNode = Node<GreetingNodeData, "greeting">;
export type TasksNode = Node<TasksNodeData, "tasks">;
export type MemoryNode = Node<MemoryNodeData, "memory">;
export type TodayContextNode = Node<TodayContextNodeData, "todayContext">;
export type NudgeNode = Node<NudgeNodeData, "nudge">;

export interface ReflectionNodeData extends BaseNodeData {
  type: "reflection";
  agentName: string;
  reflection: Reflection | null;
  reflectionLoading: boolean;
  onExplore: (question: string) => void;
  onExploreMemory: (episodeId: number) => void;
}

export type ReflectionNode = Node<ReflectionNodeData, "reflection">;

export interface GalleryViewerNodeData extends BaseNodeData {
  type: "gallery";
  images: GalleryImage[];
  onNavigate: (path: string) => void;
  onImageClick: (images: GalleryImage[], index: number) => void;
}

export type GalleryViewerNode = Node<GalleryViewerNodeData, "gallery">;

export interface RecentChatsNodeData extends BaseNodeData {
  type: "recentChats";
  threads: Thread[];
  onPreviewThread: (threadId: number) => void;
  onOpenThread: (threadId: number) => void;
  onNewChat: () => void;
  onNavigate: (path: string) => void;
}

export interface QuickCaptureNodeData extends BaseNodeData {
  type: "quickCapture";
  onSave: (text: string) => Promise<void>;
}

export interface JournalNodeData extends BaseNodeData {
  type: "journal";
  entries: DiaryEntryData[];
  onNavigate: (path: string) => void;
  onNewEntry: () => void;
}

export type RecentChatsNode = Node<RecentChatsNodeData, "recentChats">;
export type QuickCaptureNode = Node<QuickCaptureNodeData, "quickCapture">;
export type JournalNode = Node<JournalNodeData, "journal">;

export interface SystemMonitorNodeData extends BaseNodeData {
  type: "systemMonitor";
}

export type SystemMonitorNode = Node<SystemMonitorNodeData, "systemMonitor">;

export interface NetworkNodeData extends BaseNodeData {
  type: "network";
}

export type NetworkNode = Node<NetworkNodeData, "network">;

export type DashboardNode =
  | ProfileNode
  | GreetingNode
  | TasksNode
  | MemoryNode
  | TodayContextNode
  | NudgeNode
  | ReflectionNode
  | GalleryViewerNode
  | RecentChatsNode
  | QuickCaptureNode
  | JournalNode
  | SystemMonitorNode
  | NetworkNode;
