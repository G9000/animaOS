import { createHash } from "node:crypto";

const DEFAULT_SERVER_URL = "http://127.0.0.1:3031/api";
const BROKER_SECRET_ENV = "ANIMA_CREDENTIAL_BROKER_SECRET";
const CREDENTIAL_PREFIX = "anima-credential:v1:";

export interface SecretStore {
  get<T>(key: string, userId?: number): Promise<T | null>;
  set<T>(key: string, value: T): Promise<void>;
  delete(key: string): Promise<void>;
}

export interface ModCredentialStore {
  reference(scope: string, name: string): string;
  put(reference: string, secret: string): Promise<void>;
  resolve(references: string[], userId?: number): Promise<Record<string, string>>;
  delete(reference: string): Promise<void>;
}

function validateComponent(value: string): void {
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)) {
    throw new Error("Invalid credential reference component");
  }
}

export function credentialReference(scope: string, name: string): string {
  validateComponent(scope);
  validateComponent(name);
  const digest = createHash("sha256");
  digest.update("anima-credential-reference-v1\0");
  for (const component of [scope, name]) {
    const bytes = Buffer.from(component, "utf8");
    const length = Buffer.alloc(4);
    length.writeUInt32BE(bytes.length);
    digest.update(length);
    digest.update(bytes);
  }
  return `${CREDENTIAL_PREFIX}${digest.digest("hex")}`;
}

function validateReference(reference: string): void {
  if (!/^anima-credential:v1:[0-9a-f]{64}$/.test(reference)) {
    throw new Error("Invalid credential reference");
  }
}

export class CredentialBroker implements ModCredentialStore {
  private readonly audience: string;
  private readonly baseUrl: string;
  private readonly bootstrapSecret: string;

  constructor(
    modId: string,
    options: { baseUrl?: string; bootstrapSecret?: string } = {},
  ) {
    if (!/^[a-z0-9][a-z0-9.-]{0,126}$/.test(modId)) {
      throw new Error("Invalid mod credential audience");
    }
    this.audience = `anima-mod:${modId}`;
    this.baseUrl = (options.baseUrl ?? DEFAULT_SERVER_URL).replace(/\/$/, "");
    this.bootstrapSecret = (
      options.bootstrapSecret ?? process.env[BROKER_SECRET_ENV] ?? ""
    ).trim();
  }

  reference(scope: string, name: string): string {
    return credentialReference(`${this.audience}.${scope}`, name);
  }

  async put(reference: string, secret: string): Promise<void> {
    validateReference(reference);
    if (!secret) throw new Error("Credential values must be non-empty");
    await this.request("/credentials/secrets", {
      method: "POST",
      body: JSON.stringify({ audience: this.audience, reference, secret }),
    });
  }

  async resolve(references: string[], userId = 0): Promise<Record<string, string>> {
    if (references.length === 0 || references.length > 32) {
      throw new Error("Credential resolution requires a bounded non-empty scope");
    }
    references.forEach(validateReference);
    const issued = await this.request<{ capability: string }>(
      "/credentials/capabilities",
      {
        method: "POST",
        body: JSON.stringify({
          audience: this.audience,
          userId,
          references,
          ttlSeconds: 15,
        }),
      },
    );
    const redeemed = await this.request<{ secrets: Record<string, string> }>(
      "/credentials/redeem",
      {
        method: "POST",
        body: JSON.stringify({
          audience: this.audience,
          userId,
          capability: issued.capability,
        }),
      },
    );
    return redeemed.secrets;
  }

  async delete(reference: string): Promise<void> {
    validateReference(reference);
    await this.request("/credentials/secrets", {
      method: "DELETE",
      body: JSON.stringify({ audience: this.audience, reference }),
    });
  }

  private async request<T = void>(path: string, init: RequestInit): Promise<T> {
    if (!this.bootstrapSecret) {
      throw new Error(
        `${BROKER_SECRET_ENV} is required; plaintext credential fallback is disabled`,
      );
    }
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        "content-type": "application/json",
        "x-anima-credential-broker": this.bootstrapSecret,
      },
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({})) as { detail?: string };
      throw new Error(payload.detail ?? `Credential broker returned ${response.status}`);
    }
    if (response.status === 204) return undefined as T;
    return await response.json() as T;
  }
}

export class BrokerSecretStore implements SecretStore {
  constructor(
    private readonly broker: ModCredentialStore,
    private readonly scope: string,
  ) {}

  async get<T>(key: string, userId = 0): Promise<T | null> {
    const reference = this.broker.reference(this.scope, key);
    try {
      const values = await this.broker.resolve([reference], userId);
      return JSON.parse(values[reference]) as T;
    } catch (error) {
      if (error instanceof Error && error.message.includes("unavailable secret")) {
        return null;
      }
      throw error;
    }
  }

  async set<T>(key: string, value: T): Promise<void> {
    const reference = this.broker.reference(this.scope, key);
    await this.broker.put(reference, JSON.stringify(value));
  }

  async delete(key: string): Promise<void> {
    await this.broker.delete(this.broker.reference(this.scope, key));
  }
}
