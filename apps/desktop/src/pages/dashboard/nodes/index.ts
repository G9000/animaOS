import type { NodeTypes } from "@xyflow/react";
import { ProfileNode } from "./ProfileNode";
import { GreetingNode } from "./GreetingNode";
import { TasksNode } from "./TasksNode";
import { MemoryNode } from "./MemoryNode";
import { TodayContextNode } from "./TodayContextNode";
import { NudgeNode } from "./NudgeNode";
import { ReflectionNode } from "./ReflectionNode";
import { GalleryViewerNode } from "./GalleryViewerNode";
import { RecentChatsNode } from "./RecentChatsNode";
import { QuickCaptureNode } from "./QuickCaptureNode";
import { JournalNode } from "./JournalNode";

export const dashboardNodeTypes = {
  profile: ProfileNode,
  greeting: GreetingNode,
  tasks: TasksNode,
  memory: MemoryNode,
  todayContext: TodayContextNode,
  nudge: NudgeNode,
  reflection: ReflectionNode,
  gallery: GalleryViewerNode,
  recentChats: RecentChatsNode,
  quickCapture: QuickCaptureNode,
  journal: JournalNode,
} satisfies NodeTypes;

export * from "./node-types";
export { NodeShell, type NodeAction } from "./NodeShell";
