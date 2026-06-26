import {
  BaseEdge,
  getBezierPath,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";

export interface AgentPulseEdgeData extends Record<string, unknown> {
  stroke: string;
  orbColor: string;
  strokeWidth: number;
  strokeOpacity: number;
  duration: string;
  delay?: string;
}

export type AgentPulseEdgeType = Edge<AgentPulseEdgeData, "agentPulse">;

export function AgentPulseEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  data,
}: EdgeProps<AgentPulseEdgeType>) {
  const gradId = `epg-${id.replace(/[^a-zA-Z0-9_-]/g, "")}`;

  const [edgePath] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const stroke = data?.stroke ?? "var(--accent)";
  const orbColor = data?.orbColor ?? stroke;
  const strokeWidth = data?.strokeWidth ?? 1.5;
  const strokeOpacity = data?.strokeOpacity ?? 0.64;
  const duration = data?.duration ?? "2.4s";
  const delay = data?.delay ?? "0s";

  return (
    <>
      <defs>
        <linearGradient
          id={gradId}
          gradientUnits="userSpaceOnUse"
          x1={sourceX}
          y1={sourceY}
          x2={targetX}
          y2={targetY}
        >
          <stop offset="0%" stopColor={stroke} stopOpacity={0.15} />
          <stop offset="50%" stopColor={stroke} stopOpacity={strokeOpacity} />
          <stop offset="100%" stopColor={stroke} stopOpacity={0.4} />
        </linearGradient>
      </defs>

      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        className="agent-pulse-edge-path"
        style={{
          stroke: `url(#${gradId})`,
          strokeWidth,
          strokeDasharray: "none",
        }}
      />

      {/* Flowing dashes — directional cue between orb passes */}
      <path
        d={edgePath}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth * 0.55}
        strokeDasharray="3 10"
        strokeOpacity={0.28}
        className="agent-pulse-dash"
      >
        <animate
          attributeName="stroke-dashoffset"
          from="13"
          to="0"
          dur="0.9s"
          repeatCount="indefinite"
          calcMode="linear"
        />
      </path>

      <circle className="agent-pulse-orb-halo" r="7" fill={orbColor}>
        <animateMotion dur={duration} begin={delay} repeatCount="indefinite" path={edgePath} />
        <animate attributeName="r" values="5;10;5" dur="1.4s" begin={delay} repeatCount="indefinite" />
        <animate attributeName="opacity" values="0.1;0.28;0.1" dur="1.4s" begin={delay} repeatCount="indefinite" />
      </circle>

      <circle className="agent-pulse-orb" r="3.5" fill={orbColor}>
        <animateMotion dur={duration} begin={delay} repeatCount="indefinite" path={edgePath} />
        <animate attributeName="r" values="2.8;4.2;2.8" dur="1.4s" begin={delay} repeatCount="indefinite" />
      </circle>
    </>
  );
}
