import type { SchemaViewProps } from "../types";

export function SchemaView({
  tableData,
  schemaColumns,
  schemaIndexes,
  onSetView,
}: SchemaViewProps) {
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <button
          onClick={() => onSetView("rows")}
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← Back
        </button>
        <h2 className="font-mono text-sm font-medium">
          {tableData.table} — Schema
        </h2>
      </div>

      {/* Columns */}
      <div className="bg-card border border-border rounded-none overflow-hidden">
        <div className="px-4 py-2 bg-input border-b border-border text-xs font-medium">
          Columns ({schemaColumns.length})
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead className="bg-card">
              <tr className="text-muted-foreground">
                <th className="px-4 py-2 text-left">Name</th>
                <th className="px-4 py-2 text-left">Type</th>
                <th className="px-4 py-2 text-left">Nullable</th>
                <th className="px-4 py-2 text-left">Default</th>
                <th className="px-4 py-2 text-center w-16">PK</th>
              </tr>
            </thead>
            <tbody>
              {schemaColumns.map((col) => (
                <tr key={col.name} className="border-t border-border/50">
                  <td className="px-4 py-2 font-mono">{col.name}</td>
                  <td className="px-4 py-2 text-muted-foreground">{col.type}</td>
                  <td className="px-4 py-2 text-muted-foreground">
                    {col.nullable ? "YES" : "NO"}
                  </td>
                  <td className="px-4 py-2 text-muted-foreground">
                    {col.default ?? "—"}
                  </td>
                  <td className="px-4 py-2 text-center">
                    {col.primaryKey && (
                      <span className="text-primary" title="Primary Key">
                        ★
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Indexes */}
      {schemaIndexes.length > 0 && (
        <div className="bg-card border border-border rounded-none p-4">
          <h3 className="text-xs font-medium mb-3">
            Indexes ({schemaIndexes.length})
          </h3>
          <div className="flex flex-wrap gap-2">
            {schemaIndexes.map((idx) => (
              <span
                key={idx}
                className="px-2 py-1 text-[11px] bg-input border border-border rounded"
              >
                {idx}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Primary Keys Info */}
      {tableData.primaryKeys.length > 0 && (
        <div className="bg-card border border-border rounded-none p-4">
          <h3 className="text-xs font-medium mb-2">Primary Key</h3>
          <p className="text-sm font-mono text-primary">
            {tableData.primaryKeys.join(", ")}
          </p>
        </div>
      )}
    </div>
  );
}
