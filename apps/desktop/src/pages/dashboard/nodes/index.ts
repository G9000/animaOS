import type { NodeTypes } from "@xyflow/react";
import { ProfileNode } from "./ProfileNode";
import { GreetingNode } from "./GreetingNode";
import { TasksNode } from "./TasksNode";
import { MemoryNode } from "./MemoryNode";
import { MoodNode } from "./MoodNode";
import { TodayContextNode } from "./TodayContextNode";
import { NudgeNode } from "./NudgeNode";
import { ReflectionNode } from "./ReflectionNode";

export const dashboardNodeTypes = {
  profile: ProfileNode,
  greeting: GreetingNode,
  tasks: TasksNode,
  memory: MemoryNode,
  mood: MoodNode,
  todayContext: TodayContextNode,
  nudge: NudgeNode,
  reflection: ReflectionNode,
} satisfies NodeTypes;

export * from "./node-types";
export { NodeShell, type NodeAction } from "./NodeShell";
