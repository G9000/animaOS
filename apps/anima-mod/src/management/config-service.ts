import { eq } from "drizzle-orm";
import { modConfig } from "../db/schema.js";
import type { ModConfigSchema } from "../core/types.js";

import type { BunSQLiteDatabase } from "drizzle-orm/bun-sqlite";
import type * as schema from "../db/schema.js";

export class ConfigService {
  constructor(private db: BunSQLiteDatabase<typeof schema>) {}

  async getConfig(
    modId: string,
    opts: { maskSecrets?: boolean } = {}
  ): Promise<Record<string, unknown>> {
    const rows = this.db
      .select()
      .from(modConfig)
      .where(eq(modConfig.modId, modId))
      .all();

    const result: Record<string, unknown> = {};
    for (const row of rows) {
      if (opts.maskSecrets && row.isSecret) {
        result[row.key] = "***";
      } else {
        result[row.key] = JSON.parse(row.value);
      }
    }
    return result;
  }

  async setConfig(
    modId: string,
    values: Record<string, unknown>,
    schema?: ModConfigSchema
  ): Promise<void> {
    if (schema) {
      this.validate(values, schema);
    }

    for (const [key, value] of Object.entries(values)) {
      const isSecret = schema?.[key]?.type === "secret";
      this.db
        .insert(modConfig)
        .values({
          modId,
          key,
          value: JSON.stringify(value),
          isSecret,
        })
        .onConflictDoUpdate({
          target: [modConfig.modId, modConfig.key],
          set: {
            value: JSON.stringify(value),
            isSecret,
            updatedAt: new Date().toISOString(),
          },
        })
        .run();
    }
  }

  async hasConfig(modId: string): Promise<boolean> {
    const rows = this.db
      .select()
      .from(modConfig)
      .where(eq(modConfig.modId, modId))
      .all();
    return rows.length > 0;
  }

  private validate(values: Record<string, unknown>, schema: ModConfigSchema): void {
    for (const [key, field] of Object.entries(schema)) {
      const val = values[key];

      if (field.required && (val === undefined || val === null || val === "")) {
        throw new Error(`Field '${key}' is required`);
      }

      if (val !== undefined && field.type === "enum" && field.options) {
        if (!field.options.includes(String(val))) {
          throw new Error(`Invalid value for '${key}': must be one of ${field.options.join(", ")}`);
        }
      }
    }
  }
}
