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

export type NodeType =
  | "profile"
  | "greeting"
  | "tasks"
  | "memory"
  | "mood"
  | "todayContext"
  | "nudge"
  | "reflection";

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

export interface MoodNodeData extends BaseNodeData {
  type: "mood";
  mood: EmotionalContextData | null;
  agentName: string;
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
export type MoodNode = Node<MoodNodeData, "mood">;
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

export type DashboardNode =
  | ProfileNode
  | GreetingNode
  | TasksNode
  | MemoryNode
  | MoodNode
  | TodayContextNode
  | NudgeNode
  | ReflectionNode;
