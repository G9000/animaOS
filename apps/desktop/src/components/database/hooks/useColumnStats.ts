import { useState, useCallback } from "react";
import type { DbTableData } from "@anima/api-client";

export interface ColumnStats {
  name: string;
  type: string;
  nullCount: number;
  uniqueCount: number;
  min?: string;
  max?: string;
  avg?: string;
  sampleValues: string[];
}

export function useColumnStats() {
  const [columnStats, setColumnStats] = useState<ColumnStats[]>([]);

  const calculateStats = useCallback((data: DbTableData) => {
    const stats: ColumnStats[] = data.columns.map((col) => {
      const values = data.rows
        .map((r) => r[col])
        .filter((v) => v !== null && v !== undefined);
      const nullCount = data.rows.length - values.length;
      const uniqueValues = [...new Set(values.map(String))];

      // Sample up to 5 unique values
      const sampleValues = uniqueValues.slice(0, 5);

      // Try to calculate min/max/avg for numeric columns
      let min: string | undefined;
      let max: string | undefined;
      let avg: string | undefined;

      const numericValues = values
        .map((v) => Number(v))
        .filter((v) => !isNaN(v));
      if (numericValues.length > 0) {
        min = String(Math.min(...numericValues));
        max = String(Math.max(...numericValues));
        avg = String(
          (
            numericValues.reduce((a, b) => a + b, 0) / numericValues.length
          ).toFixed(2)
        );
      }

      return {
        name: col,
        type: typeof values[0] === "number" ? "number" : "text",
        nullCount,
        uniqueCount: uniqueValues.length,
        min,
        max,
        avg,
        sampleValues,
      };
    });
    setColumnStats(stats);
  }, []);

  return { columnStats, calculateStats, setColumnStats };
}
