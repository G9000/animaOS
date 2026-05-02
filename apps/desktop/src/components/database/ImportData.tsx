import { useState, useRef } from "react";
import { Icons } from "./Icons";

export type ImportFormat = "csv" | "json";

interface ImportDataProps {
  tableName: string;
  columns: string[];
  onImport: (rows: Record<string, unknown>[]) => Promise<void>;
}

interface ParsedRow {
  data: Record<string, unknown>;
  valid: boolean;
  errors: string[];
}

export function ImportData({ tableName, columns, onImport }: ImportDataProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [format, setFormat] = useState<ImportFormat>("csv");
  const [parsedRows, setParsedRows] = useState<ParsedRow[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [importResult, setImportResult] = useState<{ success: number; failed: number } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reset = () => {
    setParsedRows([]);
    setImportResult(null);
  };

  const handleFile = async (file: File) => {
    reset();
    const text = await file.text();
    
    if (format === "csv") {
      parseCSV(text);
    } else {
      parseJSON(text);
    }
  };

  const parseCSV = (text: string) => {
    const lines = text.split("\n").filter(line => line.trim());
    if (lines.length < 2) {
      setParsedRows([{ data: {}, valid: false, errors: ["CSV must have header and at least one data row"] }]);
      return;
    }

    const headers = parseCSVLine(lines[0]);
    const rows: ParsedRow[] = [];

    for (let i = 1; i < lines.length; i++) {
      const values = parseCSVLine(lines[i]);
      const data: Record<string, unknown> = {};
      const errors: string[] = [];

      headers.forEach((header, idx) => {
        if (columns.includes(header)) {
          data[header] = values[idx] ?? null;
        }
      });

      // Check for missing required columns
      const missingColumns = columns.filter(col => !(col in data));
      if (missingColumns.length > 0) {
        errors.push(`Missing columns: ${missingColumns.join(", ")}`);
      }

      rows.push({ data, valid: errors.length === 0, errors });
    }

    setParsedRows(rows);
  };

  const parseCSVLine = (line: string): string[] => {
    const values: string[] = [];
    let current = "";
    let inQuotes = false;

    for (let i = 0; i < line.length; i++) {
      const char = line[i];
      const nextChar = line[i + 1];

      if (char === '"') {
        if (inQuotes && nextChar === '"') {
          current += '"';
          i++;
        } else {
          inQuotes = !inQuotes;
        }
      } else if (char === ',' && !inQuotes) {
        values.push(current.trim());
        current = "";
      } else {
        current += char;
      }
    }
    values.push(current.trim());
    return values;
  };

  const parseJSON = (text: string) => {
    try {
      const parsed = JSON.parse(text);
      const rows: ParsedRow[] = [];

      const dataRows = Array.isArray(parsed) ? parsed : [parsed];

      for (const item of dataRows) {
        if (typeof item !== "object" || item === null) {
          rows.push({ data: {}, valid: false, errors: ["Invalid row: must be an object"] });
          continue;
        }

        const data: Record<string, unknown> = {};
        const errors: string[] = [];

        columns.forEach(col => {
          if (col in item) {
            data[col] = item[col];
          }
        });

        const missingColumns = columns.filter(col => !(col in data));
        if (missingColumns.length > 0) {
          errors.push(`Missing columns: ${missingColumns.join(", ")}`);
        }

        rows.push({ data, valid: errors.length === 0, errors });
      }

      setParsedRows(rows);
    } catch (e) {
      setParsedRows([{ data: {}, valid: false, errors: ["Invalid JSON: " + (e instanceof Error ? e.message : "parse error")] }]);
    }
  };

  const handleImport = async () => {
    const validRows = parsedRows.filter(r => r.valid).map(r => r.data);
    if (validRows.length === 0) return;

    setIsImporting(true);
    try {
      await onImport(validRows);
      setImportResult({ success: validRows.length, failed: parsedRows.filter(r => !r.valid).length });
      setTimeout(() => {
        setIsOpen(false);
        reset();
      }, 2000);
    } catch (e) {
      setImportResult({ success: 0, failed: validRows.length });
    } finally {
      setIsImporting(false);
    }
  };

  const validCount = parsedRows.filter(r => r.valid).length;
  const invalidCount = parsedRows.filter(r => !r.valid).length;

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-1 px-2 py-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        <Icons.Upload />
        Import
      </button>

      {isOpen && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="w-[600px] max-h-[80vh] bg-card border border-border rounded-none flex flex-col">
            {/* Header */}
            <div className="px-4 py-3 border-b border-border flex items-center justify-between">
              <h3 className="text-sm font-medium">Import Data into {tableName}</h3>
              <button onClick={() => { setIsOpen(false); reset(); }} className="p-1 text-muted-foreground/50 hover:text-foreground">
                <Icons.X />
              </button>
            </div>

            {/* Content */}
            <div className="p-4 overflow-auto flex-1">
              {/* Format Selection */}
              <div className="flex gap-2 mb-4">
                <button
                  onClick={() => { setFormat("csv"); reset(); }}
                  className={`px-3 py-1.5 text-xs rounded ${format === "csv" ? "bg-primary/20 text-primary" : "bg-input text-muted-foreground"}`}
                >
                  CSV
                </button>
                <button
                  onClick={() => { setFormat("json"); reset(); }}
                  className={`px-3 py-1.5 text-xs rounded ${format === "json" ? "bg-primary/20 text-primary" : "bg-input text-muted-foreground"}`}
                >
                  JSON
                </button>
              </div>

              {/* Upload Area */}
              {parsedRows.length === 0 && (
                <div
                  onClick={() => fileInputRef.current?.click()}
                  onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
                  onDragLeave={() => setIsDragging(false)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setIsDragging(false);
                    const file = e.dataTransfer.files[0];
                    if (file) handleFile(file);
                  }}
                  className={`border border-dashed rounded-none p-8 text-center cursor-pointer transition-colors ${
                    isDragging ? "border-primary bg-primary/5" : "border-border hover:border-text-muted"
                  }`}
                >
                  <Icons.Upload />
                  <p className="mt-2 text-sm text-muted-foreground">
                    Drop file here or click to browse
                  </p>
                  <p className="text-xs text-muted-foreground/50 mt-1">
                    Supports {format.toUpperCase()} format
                  </p>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept={format === "csv" ? ".csv" : ".json"}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) handleFile(file);
                    }}
                    className="hidden"
                  />
                </div>
              )}

              {/* Column Info */}
              <div className="mt-4 p-3 bg-input rounded text-xs">
                <span className="text-muted-foreground">Expected columns: </span>
                <span className="font-mono">{columns.join(", ")}</span>
              </div>

              {/* Preview */}
              {parsedRows.length > 0 && (
                <div className="mt-4">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm font-medium">
                      Preview ({parsedRows.length} rows)
                    </span>
                    <div className="flex gap-3 text-xs">
                      <span className="text-green-500">{validCount} valid</span>
                      {invalidCount > 0 && <span className="text-destructive">{invalidCount} invalid</span>}
                    </div>
                  </div>

                  <div className="border border-border rounded overflow-hidden max-h-48 overflow-auto">
                    <table className="w-full text-[11px]">
                      <thead className="bg-input sticky top-0">
                        <tr>
                          <th className="px-2 py-1 text-left w-16">Status</th>
                          {columns.slice(0, 4).map(col => (
                            <th key={col} className="px-2 py-1 text-left">{col}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {parsedRows.slice(0, 20).map((row, i) => (
                          <tr key={i} className="border-t border-border/30">
                            <td className="px-2 py-1">
                              {row.valid ? (
                                <span className="text-green-500">✓</span>
                              ) : (
                                <span className="text-destructive" title={row.errors.join(", ")}>✗</span>
                              )}
                            </td>
                            {columns.slice(0, 4).map(col => (
                              <td key={col} className="px-2 py-1 truncate max-w-[100px]">
                                {String(row.data[col] ?? "")}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Result */}
              {importResult && (
                <div className={`mt-4 p-3 rounded text-sm ${importResult.failed === 0 ? "bg-green-500/10 text-green-500" : "bg-destructive/10 text-destructive"}`}>
                  Imported {importResult.success} rows
                  {importResult.failed > 0 && `, ${importResult.failed} failed`}
                </div>
              )}
            </div>

            {/* Footer */}
            {parsedRows.length > 0 && !importResult && (
              <div className="px-4 py-3 border-t border-border flex justify-end gap-2">
                <button
                  onClick={reset}
                  className="px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
                >
                  Clear
                </button>
                <button
                  onClick={handleImport}
                  disabled={validCount === 0 || isImporting}
                  className="px-4 py-1.5 bg-primary text-white rounded text-xs hover:bg-primary/90 disabled:opacity-30"
                >
                  {isImporting ? "Importing..." : `Import ${validCount} rows`}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
