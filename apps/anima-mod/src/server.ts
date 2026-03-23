/**
 * anima-mod: Elysia Server
 *
 * Creates and configures the Elysia server with all module routes.
 */

import { Elysia } from "elysia";
import { ModRegistry } from "./core/registry.js";
import { createLogger } from "./core/logger.js";
import { createManagementRouter } from "./management/router.js";
import { createWsRouter } from "./management/ws.js";

const logger = createLogger("server");

export interface ServerOptions {
  port: number;
  hostname: string;
}

export async function createServer(opts: ServerOptions): Promise<Elysia> {
  const app = new Elysia();

  // Health check
  app.get("/", () => ({
    name: "anima-mod",
    version: "0.1.0",
    status: "running",
  }));

  app.get("/health", () => ({
    status: "healthy",
    service: "anima-mod",
    timestamp: new Date().toISOString(),
  }));

  // Load and register modules
  const registry = new ModRegistry();
  registry.initServices();

  logger.info("Loading modules...");
  await registry.loadFromConfig();

  // Migrate YAML config to DB on first boot
  await registry.migrateYamlConfig();

  logger.info("Initializing modules...");
  await registry.initAll();

  // Mount management API
  const managementRouter = createManagementRouter({
    mods: registry.getAll(),
    configService: registry.getConfigService()!,
    stateService: registry.getStateService()!,
    eventService: registry.getEventService()!,
    onRestart: (id) => registry.restartMod(id),
    onEnable: (id) => registry.initMod(id).then(() => registry.startMod(id)),
    onDisable: (id) => registry.stopMod(id),
  });
  app.use(managementRouter);

  // Mount WebSocket events
  app.use(createWsRouter());

  // Mount module routes
  const modServer = registry.getServer();
  app.use(modServer);

  // Start modules after server is ready
  logger.info("Starting modules...");
  await registry.startAll();

  // Graceful shutdown
  const shutdown = async () => {
    logger.info("Shutting down...");
    await registry.stopAll();
    process.exit(0);
  };

  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);

  return app;
}

// Export the App type for Eden Treaty
export type App = Awaited<ReturnType<typeof createServer>>;
