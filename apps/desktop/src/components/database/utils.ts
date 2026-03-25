import type { DbQueryResult } from "@anima/api-client";
import type { ColumnFilter } from "./ColumnFilter";

export function isEncryptedValue(value: unknown): boolean {
  if (typeof value !== "string") return false;
  return value.startsWith("enc1:") || value.startsWith("enc2:");
}

export function convertToCsv(result: DbQueryResult): string {
  if (result.columns.length === 0) return "";
  const escape = (val: unknown) => {
    if (val === null) return "NULL";
    const str = String(val);
    if (str.includes(",") || str.includes('"') || str.includes("\n"))
      return `"${str.replace(/"/g, '""')}"`;
    return str;
  };
  const lines = [
    result.columns.join(","),
    ...result.rows.map((row) =>
      result.columns.map((col) => escape(row[col])).join(",")
    ),
  ];
  return lines.join("\n");
}

export function generateInsertSQL(
  table: string,
  columns: string[],
  rows: Record<string, unknown>[]
): string {
  return rows
    .map((row) => {
      const vals = columns.map((col) => {
        const val = row[col];
        if (val === null) return "NULL";
        if (typeof val === "string")
          return `'${val.replace(/'/g, "''")}'`;
        return String(val);
      });
      return `INSERT INTO "${table}" (${columns
        .map((c) => `"${c}"`)
        .join(", ")}) VALUES (${vals.join(", ")});`;
    })
    .join("\n");
}

export function downloadFile(content: string, filename: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

export function formatDate(timestamp: number): string {
  return new Date(timestamp).toLocaleString();
}

// Apply column filters to rows
export function applyColumnFilters(
  rows: Record<string, unknown>[],
  filters: ColumnFilter[]
): Record<string, unknown>[] {
  if (filters.length === 0) return rows;

  return rows.filter((row) => {
    return filters.every((filter) => {
      const value = row[filter.column];
      const strValue = value == null ? "" : String(value);
      const numValue = Number(value);

      switch (filter.operator) {
        case "equals":
          return strValue === filter.value;
        case "notEquals":
          return strValue !== filter.value;
        case "contains":
          return strValue.toLowerCase().includes(filter.value.toLowerCase());
        case "startsWith":
          return strValue.toLowerCase().startsWith(filter.value.toLowerCase());
        case "endsWith":
          return strValue.toLowerCase().endsWith(filter.value.toLowerCase());
        case "greaterThan":
          return !isNaN(numValue) && numValue > Number(filter.value);
        case "lessThan":
          return !isNaN(numValue) && numValue < Number(filter.value);
        case "isNull":
          return value === null || value === undefined;
        case "isNotNull":
          return value !== null && value !== undefined;
        default:
          return true;
      }
    });
  });
}
