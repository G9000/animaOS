import { MarkerType, type EdgeTypes } from "@xyflow/react";
import { AgentAvatarNode } from "./AgentAvatarNode";
import { AgentTextNode } from "./AgentTextNode";
import { AgentBirthdayNode } from "./AgentBirthdayNode";
import { AgentRelationshipNode } from "./AgentRelationshipNode";
import { AgentNameNode } from "./AgentNameNode";
import { AgentPreviewNode } from "./AgentPreviewNode";
import {
  AgentPulseEdge,
  type AgentPulseEdgeData,
  type AgentPulseEdgeType,
} from "./AgentPulseEdge";

export * from "./types";
export type { AgentPulseEdgeType } from "./AgentPulseEdge";

export const nodeTypes = {
  agentAvatar: AgentAvatarNode,
  agentName: AgentNameNode,
  agentText: AgentTextNode,
  agentBirthday: AgentBirthdayNode,
  agentRelationship: AgentRelationshipNode,
  agentPreview: AgentPreviewNode,
};

export const edgeTypes = {
  agentPulse: AgentPulseEdge,
} satisfies EdgeTypes;

const accent = (delay: string): AgentPulseEdgeData => ({
  stroke: "var(--accent)",
  orbColor: "var(--accent)",
  strokeWidth: 1.5,
  strokeOpacity: 0.64,
  duration: "3s",
  delay,
});

export const EDGES: AgentPulseEdgeType[] = [
  {
    id: "identity-preview",
    source: "identity", target: "preview",
    type: "agentPulse",
    className: "agent-setting-edge",
    data: accent("0s"),
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 },
  },
  {
    id: "persona-preview",
    source: "persona", target: "preview",
    type: "agentPulse",
    className: "agent-setting-edge",
    data: accent("0.3s"),
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 },
  },
  {
    id: "relationship-preview",
    source: "relationship", target: "preview",
    type: "agentPulse",
    className: "agent-setting-edge",
    data: accent("0.6s"),
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 },
  },
  {
    id: "birthday-preview",
    source: "birthday", target: "preview",
    type: "agentPulse",
    className: "agent-setting-edge",
    data: accent("0.9s"),
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 },
  },
  {
    id: "agentname-preview",
    source: "agentName", target: "preview",
    type: "agentPulse",
    className: "agent-setting-edge",
    data: accent("1.2s"),
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 },
  },
  {
    id: "avatar-preview",
    source: "avatar", target: "preview",
    type: "agentPulse",
    className: "agent-setting-edge",
    data: accent("1.5s"),
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 },
  },
  {
    id: "origin-preview",
    source: "origin", target: "preview",
    type: "agentPulse",
    className: "agent-setting-edge",
    data: accent("1.65s"),
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 },
  },
  {
    id: "directive-preview",
    source: "directive", target: "preview",
    type: "agentPulse",
    className: "agent-setting-edge",
    data: accent("1.8s"),
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 },
  },
  {
    id: "autonomy-preview",
    source: "autonomy", target: "preview",
    type: "agentPulse",
    className: "agent-setting-edge",
    data: accent("2.1s"),
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 },
  },
  {
    id: "growth-preview",
    source: "growth", target: "preview",
    type: "agentPulse",
    className: "agent-setting-edge",
    data: accent("2.4s"),
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 },
  },
  {
    id: "revision-preview",
    source: "revision", target: "preview",
    type: "agentPulse",
    className: "agent-setting-edge",
    data: accent("2.55s"),
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 },
  },
  {
    id: "intentions-preview",
    source: "intentions", target: "preview",
    type: "agentPulse",
    className: "agent-setting-edge",
    data: accent("2.7s"),
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 },
  },
];
