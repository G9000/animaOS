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
  
  // Create shared services
  const anima = new AnimaApiClient({
    baseUrl: animaConfig.baseUrl ?? "http://127.0.0.1:3031/api",
    username: animaConfig.username ?? "",
    password: animaConfig.password ?? "",
  });

  const store = new ModStoreImpl(modId);

  const dispatch = DispatchBusImpl.getInstance();

  return {
    modId,
    config: modConfig,
    logger,
    anima,
    store,
    dispatch,
  };
}
