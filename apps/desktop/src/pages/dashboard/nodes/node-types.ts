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
} from "@anima/api-client";

export interface GalleryImage {
  id: string;
  url: string;
  mimeType: string;
  filename: string | null;
  caption: string | null;
  createdAt: string | null;
  source: "chat" | "diary";
}

export type NodeType =
  | "profile"
  | "greeting"
  | "tasks"
  | "memory"
  | "mood"
  | "todayContext"
  | "nudge"
  | "reflection"
  | "gallery";

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
}

export interface GreetingNodeData extends BaseNodeData {
  type: "greeting";
  agentName: string;
  brief: Greeting | null;
  briefLoading: boolean;
  userName?: string;
  onChat: () => void;
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

export type DashboardNode =
  | ProfileNode
  | GreetingNode
  | TasksNode
  | MemoryNode
  | TodayContextNode
  | NudgeNode
  | ReflectionNode
  | GalleryViewerNode;
