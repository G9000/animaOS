import { useMemo, useState } from "react";
import { Icons } from "./Icons";

type ChartType = "bar" | "line" | "pie" | "histogram";

interface ChartData {
  labels: string[];
  values: number[];
  title: string;
  type: ChartType;
}

interface AutoChartsProps {
  columns: string[];
  rows: Record<string, unknown>[];
}

export function AutoCharts({ columns, rows }: AutoChartsProps) {
  const [selectedColumn, setSelectedColumn] = useState<string | null>(null);
  const [chartType, setChartType] = useState<ChartType>("bar");
  const [isOpen, setIsOpen] = useState(false);

  // Find numeric columns
  const numericColumns = useMemo(() => {
    return columns.filter((col) => {
      const values = rows.map((r) => r[col]).filter((v) => v !== null && v !== undefined);
      if (values.length === 0) return false;
      return values.every((v) => !isNaN(Number(v)));
    });
  }, [columns, rows]);

  // Find text columns (for pie charts)
  const textColumns = useMemo(() => {
    return columns.filter((col) => {
      const values = rows.map((r) => r[col]).filter((v) => v !== null && v !== undefined);
      if (values.length === 0) return false;
      return values.some((v) => isNaN(Number(v)));
    });
  }, [columns, rows]);

  // Generate chart data
  const chartData = useMemo<ChartData | null>(() => {
    if (!selectedColumn || rows.length === 0) return null;

    const values = rows.map((r) => r[selectedColumn]);

    if (chartType === "histogram") {
      // Numeric histogram
      const numericValues = values
        .map((v) => Number(v))
        .filter((v) => !isNaN(v));
      
      if (numericValues.length === 0) return null;

      const min = Math.min(...numericValues);
      const max = Math.max(...numericValues);
      const bucketCount = Math.min(10, Math.ceil(Math.sqrt(numericValues.length)));
      const bucketSize = (max - min) / bucketCount || 1;

      const buckets = Array(bucketCount).fill(0);
      const labels = Array(bucketCount).fill("");

      numericValues.forEach((v) => {
        const bucketIndex = Math.min(Math.floor((v - min) / bucketSize), bucketCount - 1);
        buckets[bucketIndex]++;
      });

      for (let i = 0; i < bucketCount; i++) {
        const start = min + i * bucketSize;
        const end = min + (i + 1) * bucketSize;
        labels[i] = `${start.toFixed(1)}-${end.toFixed(1)}`;
      }

      return {
        labels,
        values: buckets,
        title: `Distribution of ${selectedColumn}`,
        type: "histogram",
      };
    }

    if (chartType === "pie") {
      // Group by unique values
      const counts = new Map<string, number>();
      values.forEach((v) => {
        const key = String(v);
        counts.set(key, (counts.get(key) || 0) + 1);
      });

      // Sort by count and take top 10
      const sorted = Array.from(counts.entries())
        .sort((a, b) => b[1] - a[1])
        .slice(0, 10);

      return {
        labels: sorted.map(([k]) => k),
        values: sorted.map(([, v]) => v),
        title: `${selectedColumn} Distribution`,
        type: "pie",
      };
    }

    // Bar/Line chart for numeric data
    const numericValues = values
      .map((v) => Number(v))
      .filter((v) => !isNaN(v));

    if (numericValues.length === 0) return null;

    // Group by another column if available, or use index
    const maxPoints = Math.min(50, numericValues.length);
    const step = Math.ceil(numericValues.length / maxPoints);
    
    const sampledLabels: string[] = [];
    const sampledValues: number[] = [];

    for (let i = 0; i < numericValues.length; i += step) {
      sampledLabels.push(String(i));
      sampledValues.push(numericValues[i]);
    }

    return {
      labels: sampledLabels,
      values: sampledValues,
      title: `${selectedColumn} Values`,
      type: chartType,
    };
  }, [selectedColumn, rows, chartType]);

  const maxValue = useMemo(() => {
    if (!chartData) return 0;
    return Math.max(...chartData.values);
  }, [chartData]);

  if (numericColumns.length === 0 && textColumns.length === 0) {
    return null;
  }

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-1 px-2 py-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        <Icons.ChartBar />
        Charts
      </button>

      {isOpen && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="w-[800px] max-h-[80vh] bg-card border border-border rounded-lg flex flex-col">
            {/* Header */}
            <div className="px-4 py-3 border-b border-border flex items-center justify-between">
              <h3 className="text-sm font-medium">Auto Charts</h3>
              <button onClick={() => setIsOpen(false)} className="p-1 text-muted-foreground/50 hover:text-foreground">
                <Icons.X />
              </button>
            </div>

            {/* Controls */}
            <div className="px-4 py-3 border-b border-border flex items-center gap-3">
              <select
                value={selectedColumn || ""}
                onChange={(e) => setSelectedColumn(e.target.value || null)}
                className="bg-input border border-border rounded px-3 py-1.5 text-sm"
              >
                <option value="">Select column...</option>
                {numericColumns.length > 0 && (
                  <optgroup label="Numeric">
                    {numericColumns.map((col) => (
                      <option key={col} value={col}>
                        {col}
                      </option>
                    ))}
                  </optgroup>
                )}
                {textColumns.length > 0 && (
                  <optgroup label="Text (for pie charts)">
                    {textColumns.map((col) => (
                      <option key={col} value={col}>
                        {col}
                      </option>
                    ))}
                  </optgroup>
                )}
              </select>

              {selectedColumn && (
                <div className="flex gap-1">
                  {(numericColumns.includes(selectedColumn)
                    ? (["bar", "line", "histogram"] as ChartType[])
                    : (["pie"] as ChartType[])
                  ).map((type) => (
                    <button
                      key={type}
                      onClick={() => setChartType(type)}
                      className={`px-2 py-1 text-xs rounded capitalize ${
                        chartType === type
                          ? "bg-primary/20 text-primary"
                          : "bg-input text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {type}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Chart */}
            <div className="flex-1 p-4 overflow-auto">
              {!chartData ? (
                <div className="h-64 flex items-center justify-center text-muted-foreground/50">
                  Select a column to visualize
                </div>
              ) : (
                <div className="space-y-4">
                  <h4 className="text-sm font-medium text-center">{chartData.title}</h4>

                  {chartType === "pie" ? (
                    <PieChart data={chartData} />
                  ) : (
                    <BarChart data={chartData} maxValue={maxValue} />
                  )}

                  {/* Stats */}
                  <div className="grid grid-cols-4 gap-3 mt-4">
                    <StatCard label="Count" value={chartData.values.reduce((a, b) => a + b, 0)} />
                    <StatCard label="Min" value={Math.min(...chartData.values)} />
                    <StatCard label="Max" value={Math.max(...chartData.values)} />
                    <StatCard
                      label="Avg"
                      value={(
                        chartData.values.reduce((a, b) => a + b, 0) / chartData.values.length
                      ).toFixed(2)}
                    />
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function BarChart({ data, maxValue }: { data: ChartData; maxValue: number }) {
  return (
    <div className="space-y-1">
      {data.values.map((value, i) => {
        const percentage = maxValue > 0 ? (value / maxValue) * 100 : 0;
        return (
          <div key={i} className="flex items-center gap-2">
            <div className="w-24 text-xs text-muted-foreground truncate" title={data.labels[i]}>
              {data.labels[i]}
            </div>
            <div className="flex-1 h-5 bg-input rounded overflow-hidden">
              <div
                className="h-full bg-primary/60 rounded transition-all duration-300"
                style={{ width: `${percentage}%` }}
              />
            </div>
            <div className="w-12 text-xs text-right">{value}</div>
          </div>
        );
      })}
    </div>
  );
}

function PieChart({ data }: { data: ChartData }) {
  const total = data.values.reduce((a, b) => a + b, 0);
  const colors = [
    "#3b82f6",
    "#10b981",
    "#f59e0b",
    "#ef4444",
    "#8b5cf6",
    "#ec4899",
    "#06b6d4",
    "#84cc16",
    "#f97316",
    "#6366f1",
  ];

  let currentAngle = 0;

  return (
    <div className="flex items-center gap-8">
      <svg width="200" height="200" viewBox="0 0 200 200">
        {data.values.map((value, i) => {
          const percentage = total > 0 ? value / total : 0;
          const angle = percentage * 360;
          const startAngle = currentAngle;
          currentAngle += angle;

          const x1 = 100 + 80 * Math.cos((startAngle * Math.PI) / 180);
          const y1 = 100 + 80 * Math.sin((startAngle * Math.PI) / 180);
          const x2 = 100 + 80 * Math.cos(((startAngle + angle) * Math.PI) / 180);
          const y2 = 100 + 80 * Math.sin(((startAngle + angle) * Math.PI) / 180);

          const largeArc = angle > 180 ? 1 : 0;

          return (
            <path
              key={i}
              d={`M 100 100 L ${x1} ${y1} A 80 80 0 ${largeArc} 1 ${x2} ${y2} Z`}
              fill={colors[i % colors.length]}
              stroke="white"
              strokeWidth="2"
            />
          );
        })}
        <circle cx="100" cy="100" r="40" fill="#1a1a1a" />
      </svg>

      <div className="space-y-2">
        {data.labels.map((label, i) => (
          <div key={i} className="flex items-center gap-2 text-xs">
            <div
              className="w-3 h-3 rounded"
              style={{ backgroundColor: colors[i % colors.length] }}
            />
            <span className="truncate max-w-[150px]" title={label}>
              {label}
            </span>
            <span className="text-muted-foreground">
              {((data.values[i] / total) * 100).toFixed(1)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="p-3 bg-input rounded border border-border/50 text-center">
      <div className="text-[10px] text-muted-foreground uppercase">{label}</div>
      <div className="text-lg font-semibold mt-1">{value}</div>
    </div>
  );
}


