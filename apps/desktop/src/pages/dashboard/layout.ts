import type { Node } from "@xyflow/react";
import type {
  Greeting,
  Reflection,
  TaskItem,
  MemoryEpisodeData,
  EmotionalContextData,
  MessagePill,
  Nudge,
} from "@anima/api-client";
import type {
  DashboardNode,
  ProfileNode,
  GreetingNode,
  TasksNode,
  MemoryNode,
  NudgeNode,
  ReflectionNode,
  GalleryViewerNode,
  ProfileNodeData,
  GreetingNodeData,
  TasksNodeData,
  MemoryNodeData,
  NudgeNodeData,
  ReflectionNodeData,
  GalleryViewerNodeData,
  GalleryImage,
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
  onDismissNudge: (type: string) => void;
  onExploreMemory: (episodeId: number) => void;
  onCloseNode: (id: string) => void;
  onImageClick: (images: GalleryImage[], index: number) => void;
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
  tasks: TaskItem[];
  currentFocus?: string | null;
  episodes: MemoryEpisodeData[];
  mood: EmotionalContextData | null;
  nudges: Nudge[];
  galleryImages: GalleryImage[];
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
    tasks,
    currentFocus,
    episodes,
    mood,
    nudges,
    galleryImages,
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

  // Column 2 — tasks
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

  // Column 3 — gallery (images from journal); starts at a fixed size, user can resize
  const galleryNode = makeNode<GalleryViewerNodeData>("gallery", "gallery", {
    type: "gallery",
    images: galleryImages,
    onNavigate: callbacks.onNavigate,
    onImageClick: callbacks.onImageClick,
    onClose: () => callbacks.onCloseNode("gallery"),
  }) as GalleryViewerNode;
  galleryNode.width = COLUMN_WIDTH - 20;
  galleryNode.height = 380;
  nodes.push(galleryNode);

  // Masonry-style layout across 4 columns
  const columnHeights = [0, 0, 0, 0];
  const columnForType: Partial<Record<string, number>> = {
    profile: 0,
    greeting: 1,
    nudge: 1,
    reflection: 1,
    tasks: 2,
    memory: 3,
    gallery: 3,
  };

  const estimatedHeights: Partial<Record<string, number>> = {
    profile: 560,
    greeting: 140,
    nudge: 90 + nudges.length * 30,
    reflection: 130,
    tasks: 220,
    memory: 340,
    gallery: galleryImages.length === 0 ? 90 : Math.min(3, Math.ceil(galleryImages.length / 3)) * 108 + 56,
  };

  for (const node of nodes) {
    const col = columnForType[node.type] ?? 0;
    const h = estimatedHeights[node.type] ?? 200;
    node.position = {
      x: col * COLUMN_WIDTH,
      y: columnHeights[col],
    };
    columnHeights[col] += h + GAP_Y;
  }

  return nodes;
}
