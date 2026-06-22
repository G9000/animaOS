import { useCallback, useEffect, useState } from "react";
import type { DashboardNode } from "./nodes/node-types";

const STORAGE_KEY = "anima_dashboard_node_positions";

export function useNodePositions(nodes: DashboardNode[] | null) {
  const [hydratedNodes, setHydratedNodes] = useState<DashboardNode[] | null>(
    null,
  );

  useEffect(() => {
    if (!nodes) return;

    let saved: Record<string, { x: number; y: number }> = {};
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) saved = JSON.parse(raw);
    } catch {
      saved = {};
    }

    const next = nodes.map((node) => {
      const pos = saved[node.id];
      if (pos) {
        return { ...node, position: pos };
      }
      return node;
    });

    setHydratedNodes(next);
  }, [nodes]);

  const persistPositions = useCallback(
    (updated: DashboardNode[]) => {
      const positions: Record<string, { x: number; y: number }> = {};
      for (const node of updated) {
        positions[node.id] = node.position;
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
