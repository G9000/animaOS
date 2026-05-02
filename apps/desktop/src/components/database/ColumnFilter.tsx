import { useState } from "react";
import { Icons } from "./Icons";

export type FilterOperator = 
  | "equals" 
  | "notEquals" 
  | "contains" 
  | "startsWith" 
  | "endsWith"
  | "greaterThan"
  | "lessThan"
  | "isNull"
  | "isNotNull";

export interface ColumnFilter {
  column: string;
  operator: FilterOperator;
  value: string;
}

interface ColumnFilterPanelProps {
  columns: string[];
  activeFilters: ColumnFilter[];
  onAddFilter: (filter: ColumnFilter) => void;
  onRemoveFilter: (index: number) => void;
  onClearFilters: () => void;
}

const operatorLabels: Record<FilterOperator, string> = {
  equals: "=",
  notEquals: "≠",
  contains: "contains",
  startsWith: "starts with",
  endsWith: "ends with",
  greaterThan: ">",
  lessThan: "<",
  isNull: "is null",
  isNotNull: "is not null",
};



export function ColumnFilterPanel({
  columns,
  activeFilters,
  onAddFilter,
  onRemoveFilter,
  onClearFilters,
}: ColumnFilterPanelProps) {
  const [selectedColumn, setSelectedColumn] = useState(columns[0] || "");
  const [selectedOperator, setSelectedOperator] = useState<FilterOperator>("equals");
  const [filterValue, setFilterValue] = useState("");
  const [isOpen, setIsOpen] = useState(false);

  const handleAdd = () => {
    if (!selectedColumn) return;
    if (selectedOperator !== "isNull" && selectedOperator !== "isNotNull" && !filterValue) return;
    
    onAddFilter({
      column: selectedColumn,
      operator: selectedOperator,
      value: filterValue,
    });
    setFilterValue("");
    setIsOpen(false);
  };

  const hasValueInput = selectedOperator !== "isNull" && selectedOperator !== "isNotNull";

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`flex items-center gap-1.5 px-2 py-1 text-xs rounded transition-colors ${
          activeFilters.length > 0
            ? "bg-primary/20 text-primary border border-primary/30"
            : "text-muted-foreground hover:text-foreground border border-transparent hover:border-border"
        }`}
      >
        <Icons.Filter />
        Filter
        {activeFilters.length > 0 && (
          <span className="ml-1 px-1.5 py-0.5 bg-primary/30 rounded-none text-[10px]">
            {activeFilters.length}
          </span>
        )}
      </button>

      {isOpen && (
        <div className="absolute left-0 top-full mt-1 w-72 bg-card border border-border rounded-none z-50 p-3">
          {/* Active Filters */}
          {activeFilters.length > 0 && (
            <div className="mb-3 space-y-1.5">
              <div className="text-[10px] text-muted-foreground uppercase tracking-wide">Active</div>
              {activeFilters.map((filter, idx) => (
                <div
                  key={idx}
                  className="flex items-center justify-between px-2 py-1.5 bg-input rounded text-[11px]"
                >
                  <span className="truncate">
                    <span className="font-mono">{filter.column}</span>{" "}
                    <span className="text-muted-foreground">{operatorLabels[filter.operator]}</span>{" "}
                    {filter.value && <span className="font-mono">"{filter.value}"</span>}
                  </span>
                  <button
                    onClick={() => onRemoveFilter(idx)}
                    className="ml-2 p-0.5 text-muted-foreground/50 hover:text-destructive"
                  >
                    <Icons.X />
                  </button>
                </div>
              ))}
              <button
                onClick={onClearFilters}
                className="w-full mt-2 px-2 py-1 text-[10px] text-muted-foreground hover:text-destructive border border-dashed border-text-muted/30 rounded"
              >
                Clear all filters
              </button>
            </div>
          )}

          {/* Add New Filter */}
          <div className="space-y-2">
            <div className="text-[10px] text-muted-foreground uppercase tracking-wide">Add Filter</div>
            
            <select
              value={selectedColumn}
              onChange={(e) => setSelectedColumn(e.target.value)}
              className="w-full bg-input border border-border rounded px-2 py-1.5 text-xs"
            >
              {columns.map((col) => (
                <option key={col} value={col}>
                  {col}
                </option>
              ))}
            </select>

            <select
              value={selectedOperator}
              onChange={(e) => setSelectedOperator(e.target.value as FilterOperator)}
              className="w-full bg-input border border-border rounded px-2 py-1.5 text-xs"
            >
              {Object.entries(operatorLabels).map(([op, label]) => (
                <option key={op} value={op}>
                  {label}
                </option>
              ))}
            </select>

            {hasValueInput && (
              <input
                type="text"
                value={filterValue}
                onChange={(e) => setFilterValue(e.target.value)}
                placeholder="Value..."
                className="w-full bg-input border border-border rounded px-2 py-1.5 text-xs"
                onKeyDown={(e) => e.key === "Enter" && handleAdd()}
              />
            )}

            <button
              onClick={handleAdd}
              disabled={hasValueInput && !filterValue}
              className="w-full px-3 py-1.5 bg-primary text-white rounded text-xs hover:bg-primary/90 disabled:opacity-30"
            >
              Add Filter
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// Apply filters to rows
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
