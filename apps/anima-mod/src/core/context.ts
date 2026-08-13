/**
 * a-mod: ModContext Implementation
 * 
 * Creates the execution context for each module.
 */

import type { ModContext, Logger, AnimaClient, ModStore, DispatchBus } from "./types.js";
import { createLogger } from "./logger.js";
import { AnimaApiClient } from "./anima-client.js";
import { ModStoreImpl } from "./store.js";
import { DispatchBusImpl } from "./dispatch.js";
import { loadConfig } from "./config.js";
import { BrokerSecretStore, CredentialBroker } from "../security/credential-broker.js";

/**
 * Create a ModContext for the given module
 */
export async function createModContext(
  modId: string,
  modConfig: Record<string, unknown>
): Promise<ModContext> {
  const logger = createLogger(modId);
  
  // Load core config for anima connection
  const coreConfig = await loadConfig();
  const animaConfig = coreConfig.core?.anima ?? {};
  const coreCredentials = new CredentialBroker("core");
  const corePasswordReference = coreCredentials.reference("core-auth", "password");
  let password = animaConfig.password ?? "";
  if (!password && animaConfig.passwordRef) {
    if (animaConfig.passwordRef !== corePasswordReference) {
      throw new Error("Invalid Core password credential reference");
    }
    const stored = await coreCredentials.resolve([corePasswordReference]);
    const encoded = stored[corePasswordReference];
    if (!encoded) throw new Error("Core password credential is unavailable");
    const decoded = JSON.parse(encoded) as unknown;
    if (typeof decoded !== "string" || !decoded) {
      throw new Error("Core password credential is invalid");
    }
    password = decoded;
  }
  
  // Create shared services
  const anima = new AnimaApiClient({
    baseUrl: animaConfig.baseUrl ?? "http://127.0.0.1:3031/api",
    username: animaConfig.username ?? "",
    password,
  });

  const store = new ModStoreImpl(modId);
  const secrets = new BrokerSecretStore(new CredentialBroker(modId), "runtime-secret");

  const dispatch = DispatchBusImpl.getInstance();

  return {
    modId,
    config: modConfig,
    logger,
    anima,
    store,
    secrets,
    dispatch,
  };
}
