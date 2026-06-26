import { useCallback, useEffect, useState } from "react";
import type { DashboardNode } from "./nodes/node-types";

const STORAGE_KEY = "anima_dashboard_node_positions";

type SavedNode = { x: number; y: number; width?: number; height?: number };

export function useNodePositions(nodes: DashboardNode[] | null) {
  const [hydratedNodes, setHydratedNodes] = useState<DashboardNode[] | null>(
    null,
  );

  useEffect(() => {
    if (!nodes) return;

    let saved: Record<string, SavedNode> = {};
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) saved = JSON.parse(raw);
    } catch {
      saved = {};
    }

    const next = nodes.map((node) => {
      const s = saved[node.id];
      if (!s) return node;
      return {
        ...node,
        position: { x: s.x, y: s.y },
        ...(s.width != null ? { width: s.width } : {}),
        ...(s.height != null ? { height: s.height } : {}),
      };
    });

    setHydratedNodes(next);
  }, [nodes]);

  const persistPositions = useCallback(
    (updated: DashboardNode[]) => {
      const positions: Record<string, SavedNode> = {};
      for (const node of updated) {
        positions[node.id] = {
          x: node.position.x,
          y: node.position.y,
          ...(node.width != null ? { width: node.width } : {}),
          ...(node.height != null ? { height: node.height } : {}),
        };
      }
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(positions));
      } catch {
        // ignore
      }
      setHydratedNodes(updated);
    },
    [],
  );

  return { hydratedNodes, persistPositions };
}
