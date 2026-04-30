import { useState } from "react";
import type { ModConfigSchema } from "./types";

interface ConfigFormProps {
  schema: ModConfigSchema;
  values: Record<string, unknown>;
  onSave: (values: Record<string, unknown>) => Promise<void>;
}

function shouldShow(field: { showWhen?: Record<string, unknown> }, values: Record<string, unknown>): boolean {
  if (!field.showWhen) return true;
  return Object.entries(field.showWhen).every(([k, v]) => values[k] === v);
}

function buildSavePayload(
  schema: ModConfigSchema,
  values: Record<string, unknown>,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(values)) {
    if (schema[key]?.type === "secret" && value === "***") continue;
    payload[key] = value;
  }
  return payload;
}

export default function ConfigForm({ schema, values: initialValues, onSave }: ConfigFormProps) {
  const [values, setValues] = useState<Record<string, unknown>>(initialValues);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = (key: string, val: unknown) => setValues((prev) => ({ ...prev, [key]: val }));

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave(buildSavePayload(schema, values));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      {Object.entries(schema).map(([key, field]) => {
        if (!shouldShow(field, values)) return null;

        return (
          <div key={key}>
            <label className="block font-mono text-[9px] tracking-wider text-muted-foreground/60 mb-1">
              {field.label}
              {field.required && <span className="text-destructive ml-1">*</span>}
            </label>

            {field.description && (
              <p className="font-mono text-[8px] text-muted-foreground/30 mb-1">{field.description}</p>
            )}

            {field.type === "boolean" ? (
              <button
                onClick={() => set(key, !values[key])}
                className={`w-7 h-4 rounded-full transition-colors relative ${
                  values[key] ? "bg-primary/30" : "bg-input"
                }`}
              >
                <div
                  className={`absolute top-0.5 w-3 h-3 rounded-full transition-all ${
                    values[key] ? "left-3.5 bg-primary" : "left-0.5 bg-text-muted/30"
                  }`}
                />
              </button>
            ) : field.type === "enum" ? (
              <div className="flex gap-1">
                {field.options?.map((opt) => (
                  <button
                    key={opt}
                    onClick={() => set(key, opt)}
                    className={`font-mono text-[9px] px-2 py-1 border transition-colors ${
                      values[key] === opt
                        ? "border-primary text-primary"
                        : "border-border text-muted-foreground/40 hover:text-foreground"
                    }`}
                  >
                    {opt.toUpperCase()}
                  </button>
                ))}
              </div>
            ) : field.type === "secret" ? (
              <input
                type="password"
                value={values[key] === "***" ? "" : String(values[key] ?? "")}
                placeholder={values[key] === "***" ? "saved" : ""}
                onChange={(e) => set(key, e.target.value)}
                className="w-full bg-input border border-border px-2 py-1.5 font-mono text-[10px] text-foreground focus:border-primary/50 outline-none"
              />
            ) : field.type === "number" ? (
              <input
                type="number"
                value={String(values[key] ?? field.default ?? "")}
                onChange={(e) => set(key, Number(e.target.value))}
                className="w-full bg-input border border-border px-2 py-1.5 font-mono text-[10px] text-foreground focus:border-primary/50 outline-none"
              />
            ) : (
              <input
                type="text"
                value={String(values[key] ?? field.default ?? "")}
                onChange={(e) => set(key, e.target.value)}
                className="w-full bg-input border border-border px-2 py-1.5 font-mono text-[10px] text-foreground focus:border-primary/50 outline-none"
              />
            )}
          </div>
        );
      })}

      {error && (
        <p className="font-mono text-[8px] text-destructive">{error}</p>
      )}

      <button
        onClick={handleSave}
        disabled={saving}
        className="font-mono text-[9px] tracking-wider text-primary border border-primary/30 px-4 py-1.5 hover:bg-primary/10 transition-colors disabled:opacity-40"
      >
        {saving ? "SAVING..." : "SAVE"}
      </button>
    </div>
  );
}
