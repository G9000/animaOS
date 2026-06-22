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
import type {
  DashboardNode,
  ProfileNode,
  GreetingNode,
  TasksNode,
  MemoryNode,
  MoodNode,
  TodayContextNode,
  NudgeNode,
  ReflectionNode,
  ProfileNodeData,
  GreetingNodeData,
  TasksNodeData,
  MemoryNodeData,
  MoodNodeData,
  TodayContextNodeData,
  NudgeNodeData,
  ReflectionNodeData,
} from "./nodes/node-types";

const COLUMN_WIDTH = 340;
const GAP_Y = 24;

export interface DashboardCallbacks {
  onNavigate: (path: string) => void;
  onExplore: (thought: string, pills?: MessagePill[]) => void;
  onToggleTask: (task: TaskItem) => void;
  onDeleteTask: (id: number) => void;
  onAddTask: (text: string) => void;
  onEpisodeChat: (episode: MemoryEpisodeData) => void;
  onEpisodeRead: (episode: MemoryEpisodeData) => void;
  onViewAllEntries: () => void;
  onTodayContextSave: (context: TodayContext) => void;
  onTodayContextClear: () => void;
  onDismissNudge: (type: string) => void;
  onExploreMemory: (episodeId: number) => void;
  onCloseNode: (id: string) => void;
}

export interface DashboardInput {
  userName?: string;
  agentName: string;
  avatarUrl: string;
  relationship?: string | null;
  emotion?: string | null;
  lastSession?: string | null;
  brief: Greeting | null;
  briefLoading: boolean;
  reflection: Reflection | null;
  reflectionLoading: boolean;
  todayContext: TodayContext | null;
  todayContextLine: string | null;
  tasks: TaskItem[];
  currentFocus?: string | null;
  episodes: MemoryEpisodeData[];
  mood: EmotionalContextData | null;
  nudges: Nudge[];
}

function makeNode<Data extends Record<string, unknown>>(
  id: string,
  type: string,
  data: Data,
): Node<Data> {
  return {
    id,
    type,
    position: { x: 0, y: 0 },
    data,
  } as Node<Data>;
}

export function buildInitialNodes(
  input: DashboardInput,
  callbacks: DashboardCallbacks,
): DashboardNode[] {
  const {
    userName,
    agentName,
    avatarUrl,
    relationship,
    emotion,
    lastSession,
    brief,
    briefLoading,
    reflection,
    reflectionLoading,
    todayContext,
    todayContextLine,
    tasks,
    currentFocus,
    episodes,
    mood,
    nudges,
  } = input;

  const nodes: DashboardNode[] = [];

  // Column 0 — identity / agent
  nodes.push(
    makeNode<ProfileNodeData>("profile", "profile", {
      type: "profile",
      agentName,
      avatarUrl,
      relationship,
      emotion,
      mood,
      lastSession,
      brief,
      briefLoading,
      todayContextLine,
      currentFocus,
      onExplore: callbacks.onExplore,
      onClose: () => callbacks.onCloseNode("profile"),
    }) as ProfileNode,
  );

  // Column 1 — greeting, mood, nudges
  nodes.push(
    makeNode<GreetingNodeData>("greeting", "greeting", {
      type: "greeting",
      agentName,
      brief,
      briefLoading,
      userName,
      onChat: () => callbacks.onExplore(brief?.message ?? "", brief?.pills),
      onClose: () => callbacks.onCloseNode("greeting"),
    }) as GreetingNode,
  );

  if (mood?.dominantEmotion) {
    nodes.push(
      makeNode<MoodNodeData>("mood", "mood", {
        type: "mood",
        mood,
        agentName,
        onClose: () => callbacks.onCloseNode("mood"),
      }) as MoodNode,
    );
  }

  if (nudges.length > 0) {
    nodes.push(
      makeNode<NudgeNodeData>("nudge", "nudge", {
        type: "nudge",
        nudges,
        onDismiss: callbacks.onDismissNudge,
        onClose: () => callbacks.onCloseNode("nudge"),
      }) as NudgeNode,
    );
  }

  if (reflectionLoading || reflection?.question) {
    nodes.push(
      makeNode<ReflectionNodeData>("reflection", "reflection", {
        type: "reflection",
        agentName,
        reflection,
        reflectionLoading,
        onExplore: (question) => callbacks.onExplore(question),
        onExploreMemory: callbacks.onExploreMemory,
        onClose: () => callbacks.onCloseNode("reflection"),
      }) as ReflectionNode,
    );
  }

  // Column 2 — tasks, today context
  nodes.push(
    makeNode<TasksNodeData>("tasks", "tasks", {
      type: "tasks",
      tasks,
      currentFocus,
      onNavigate: callbacks.onNavigate,
      onToggleTask: callbacks.onToggleTask,
      onDeleteTask: callbacks.onDeleteTask,
      onAddTask: callbacks.onAddTask,
      onClose: () => callbacks.onCloseNode("tasks"),
    }) as TasksNode,
  );

  nodes.push(
    makeNode<TodayContextNodeData>("todayContext", "todayContext", {
      type: "todayContext",
      context: todayContext,
      greeting: "How are you arriving today?",
      onSave: callbacks.onTodayContextSave,
      onClear: callbacks.onTodayContextClear,
      onClose: () => callbacks.onCloseNode("todayContext"),
    }) as TodayContextNode,
  );

  // Column 3 — memories
  if (episodes.length > 0) {
    nodes.push(
      makeNode<MemoryNodeData>("memory", "memory", {
        type: "memory",
        episodes,
        agentName,
        avatarUrl,
        onChat: callbacks.onEpisodeChat,
        onRead: callbacks.onEpisodeRead,
        onViewAll: callbacks.onViewAllEntries,
        onClose: () => callbacks.onCloseNode("memory"),
      }) as MemoryNode,
    );
  }

  // Masonry-style layout across 4 columns
  const columnHeights = [0, 0, 0, 0];
  const columnForType: Record<DashboardNode["type"], number> = {
    profile: 0,
    greeting: 1,
    mood: 1,
    nudge: 1,
    reflection: 1,
    tasks: 2,
    todayContext: 2,
    memory: 3,
  };

  const estimatedHeights: Record<DashboardNode["type"], number> = {
    profile: 560,
    greeting: 140,
    mood: 160,
    nudge: 90 + nudges.length * 30,
    reflection: 130,
    tasks: 220,
    todayContext: 260,
    memory: 340,
  };

  for (const node of nodes) {
    const col = columnForType[node.type];
    node.position = {
      x: col * COLUMN_WIDTH,
      y: columnHeights[col],
    };
    columnHeights[col] += estimatedHeights[node.type] + GAP_Y;
  }

  return nodes;
}
