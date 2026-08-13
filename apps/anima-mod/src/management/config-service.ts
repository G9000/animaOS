import { and, eq } from "drizzle-orm";
import { modConfig } from "../db/schema.js";
import type { ModConfigSchema } from "../core/types.js";

import type { BunSQLiteDatabase } from "drizzle-orm/bun-sqlite";
import type * as schema from "../db/schema.js";
import {
  CredentialBroker,
  type ModCredentialStore,
} from "../security/credential-broker.js";

const CREDENTIAL_REFERENCE_PATTERN = /^anima-credential:v1:[0-9a-f]{64}$/;

export class ConfigService {
  constructor(
    private db: BunSQLiteDatabase<typeof schema>,
    private credentials: ModCredentialStore = new CredentialBroker("config"),
  ) {}

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
      } else if (row.isSecret) {
        const reference = await this.ensureSecretReference(row.modId, row.key, row.value);
        const secrets = await this.credentials.resolve([reference]);
        result[row.key] = JSON.parse(secrets[reference]);
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
      const existing = await this.getConfig(modId, { maskSecrets: false });
      this.validate({ ...existing, ...values }, schema);
    }

    for (const [key, value] of Object.entries(values)) {
      const isSecret = schema?.[key]?.type === "secret";
      if (isSecret && value === "***") continue;
      const storedValue = isSecret
        ? await this.storeSecretReference(modId, key, value)
        : JSON.stringify(value);
      this.db
        .insert(modConfig)
        .values({
          modId,
          key,
          value: storedValue,
          isSecret,
        })
        .onConflictDoUpdate({
          target: [modConfig.modId, modConfig.key],
          set: {
            value: storedValue,
            isSecret,
            updatedAt: new Date().toISOString(),
          },
        })
        .run();
    }
  }

  private async storeSecretReference(
    modId: string,
    key: string,
    value: unknown,
  ): Promise<string> {
    if (typeof value !== "string" || !value) {
      throw new Error(`Secret field '${key}' must be a non-empty string`);
    }
    const reference = this.credentials.reference("mod-config", `${modId}:${key}`);
    const encoded = JSON.stringify(value);
    await this.credentials.put(reference, encoded);
    const verified = await this.credentials.resolve([reference]);
    if (verified[reference] !== encoded) {
      throw new Error(`Secret field '${key}' failed credential verification`);
    }
    return JSON.stringify(reference);
  }

  private async ensureSecretReference(
    modId: string,
    key: string,
    rawValue: string,
  ): Promise<string> {
    const decoded = JSON.parse(rawValue);
    if (typeof decoded !== "string") {
      throw new Error(`Secret field '${key}' has an invalid stored value`);
    }
    if (CREDENTIAL_REFERENCE_PATTERN.test(decoded)) return decoded;

    const reference = this.credentials.reference("mod-config", `${modId}:${key}`);
    const encoded = JSON.stringify(decoded);
    await this.credentials.put(reference, encoded);
    const verified = await this.credentials.resolve([reference]);
    if (verified[reference] !== encoded) {
      throw new Error(`Secret field '${key}' failed migration verification`);
    }
    this.db
      .update(modConfig)
      .set({ value: JSON.stringify(reference), isSecret: true })
      .where(and(eq(modConfig.modId, modId), eq(modConfig.key, key)))
      .run();
    return reference;
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
