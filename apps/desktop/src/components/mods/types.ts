export type FieldType = "string" | "number" | "boolean" | "enum" | "secret";

export interface ConfigField {
  type: FieldType;
  label: string;
  required?: boolean;
  default?: unknown;
  options?: string[];
  showWhen?: Record<string, unknown>;
  description?: string;
}

export type ModConfigSchema = Record<string, ConfigField>;

export interface SetupStep {
  step: number;
  title: string;
  instructions?: string;
  field?: string;
  action?: "healthcheck";
}
