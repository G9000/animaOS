/**
 * a-mod: Module Registry
 *
 * Loads, initializes, and manages module lifecycle.
 */

import { Elysia } from "elysia";
import type { AnyElysia } from "elysia";
import type { Mod, ModConfig, ModContext, ModManifest } from "./types.js";
import { createModContext } from "./context.js";
import { createLogger } from "./logger.js";
import { loadConfig } from "./config.js";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";
import { getDb } from "../db/index.js";
import { ConfigService } from "../management/config-service.js";
import { StateService } from "../management/state-service.js";
import { EventService } from "../management/event-service.js";

const logger = createLogger("registry");

export interface LoadedMod {
  config: ModConfig;
  manifest?: ModManifest;
  mod?: Mod;
  ctx?: ModContext;
  router?: AnyElysia;
}

export class ModRegistry {
  private mods = new Map<string, LoadedMod>();
  private app = new Elysia();
  private configService?: ConfigService;
  private stateService?: StateService;
  private eventService?: EventService;

  /** Initialize management services (call before loadFromConfig) */
  initServices(): void {
    const db = getDb();
    this.configService = new ConfigService(db);
    this.stateService = new StateService(db);
    this.eventService = new EventService(db);
  }

  /**
   * Load all modules from anima-mod.config.yaml
   */
  async loadFromConfig(): Promise<void> {
    const config = await loadConfig();

    logger.info("Loading modules from config", {
      count: config.modules?.length ?? 0
    });

    for (const modConfig of config.modules ?? []) {
      await this.register(modConfig);
    }
  }

  /**
   * Register a single module
   */
  async register(modConfig: ModConfig): Promise<void> {
    const { id, path: modPath } = modConfig;

    if (this.mods.has(id)) {
      throw new Error(`Module ${id} already registered`);
    }

    logger.info("Registering module", { id, path: modPath });

    try {
      // Resolve path relative to project root
      const resolvedPath = resolve(modPath);
      const modUrl = pathToFileURL(join(resolvedPath, "mod.ts")).href;

      // Dynamic import
      const modModule = await import(modUrl);
      const mod: Mod = modModule.default ?? modModule.mod;

      if (!mod || typeof mod.init !== "function") {
        throw new Error(`Module ${id} does not export a valid Mod`);
      }

      // Validate ID matches
      if (mod.id !== id) {
        logger.warn(`Module ID mismatch: config says "${id}", mod exports "${mod.id}"`);
      }

      this.mods.set(id, {
        config: modConfig,
        mod,
      });

      logger.info("Module registered", { id, version: mod.version });
    } catch (err) {
      logger.error(`Failed to register module ${id}`, {
        error: err instanceof Error ? err.message : String(err)
      });
      throw err;
    }
  }

  /** Initialize a single module by ID */
  async initMod(id: string): Promise<void> {
    const loaded = this.mods.get(id);
    if (!loaded?.mod) throw new Error(`Module ${id} not registered`);

    // Get config: DB first, fall back to YAML
    let config = loaded.config.config;
    if (this.configService && await this.configService.hasConfig(id)) {
      config = await this.configService.getConfig(id);
    }

    const ctx = await createModContext(id, config);
    loaded.ctx = ctx;
    await loaded.mod.init(ctx);

    if (loaded.mod.getRouter) {
      loaded.router = loaded.mod.getRouter();
    }

    logger.info("Module initialized", { id });
  }

  /** Start a single module by ID */
  async startMod(id: string): Promise<void> {
    const loaded = this.mods.get(id);
    if (!loaded?.mod) throw new Error(`Module ${id} not registered`);

    await loaded.mod.start();
    await this.stateService?.setState(id, {
      enabled: true,
      status: "running",
      startedAt: new Date().toISOString(),
      lastError: null,
    });
    await this.eventService?.logEvent(id, "started");
    logger.info("Module started", { id });
  }

  /** Stop a single module by ID */
  async stopMod(id: string): Promise<void> {
    const loaded = this.mods.get(id);
    if (!loaded?.mod?.stop) return;

    await loaded.mod.stop();
    await this.stateService?.setState(id, {
      enabled: false,
      status: "stopped",
    });
    await this.eventService?.logEvent(id, "stopped");
    logger.info("Module stopped", { id });
  }

  /** Restart a single module */
  async restartMod(id: string): Promise<void> {
    await this.stopMod(id);
    await this.initMod(id);
    await this.startMod(id);
  }

  /** Get all loaded mods with their metadata for the management API */
  getAll(): Map<string, LoadedMod> {
    return this.mods;
  }

  getConfigService(): ConfigService | undefined {
    return this.configService;
  }

  getStateService(): StateService | undefined {
    return this.stateService;
  }

  getEventService(): EventService | undefined {
    return this.eventService;
  }

  /**
   * Initialize all registered modules
   */
  async initAll(): Promise<void> {
    logger.info("Initializing modules", { count: this.mods.size });
    const sortedIds = this.sortByDependencies();
    for (const id of sortedIds) {
      try {
        await this.initMod(id);
      } catch (err) {
        logger.error(`Failed to initialize module ${id}`, {
          error: err instanceof Error ? err.message : String(err)
        });
        throw err;
      }
    }
  }

  /**
   * Start all initialized modules
   */
  async startAll(): Promise<void> {
    logger.info("Starting modules");

    for (const [id, loaded] of this.mods) {
      if (!loaded.mod) continue;
      try {
        await this.startMod(id);
      } catch (err) {
        logger.error(`Failed to start module ${id}`, {
          error: err instanceof Error ? err.message : String(err)
        });
        await this.stateService?.setState(id, {
          status: "error",
          lastError: err instanceof Error ? err.message : String(err),
        });
      }
    }
  }

  /**
   * Stop all modules gracefully
   */
  async stopAll(): Promise<void> {
    logger.info("Stopping modules");

    for (const [id] of this.mods) {
      try {
        await this.stopMod(id);
      } catch (err) {
        logger.error(`Error stopping module ${id}`, {
          error: err instanceof Error ? err.message : String(err)
        });
      }
    }
  }

  /** One-time migration: seed DB config from YAML values for mods that have no DB config yet */
  async migrateYamlConfig(): Promise<void> {
    if (!this.configService) return;

    for (const [id, loaded] of this.mods) {
      const yamlConfig = loaded.config.config;
      if (!yamlConfig || Object.keys(yamlConfig).length === 0) continue;

      const hasDbConfig = await this.configService.hasConfig(id);
      if (hasDbConfig) continue;

      // Seed DB from YAML
      const schema = loaded.mod?.configSchema;
      await this.configService.setConfig(id, yamlConfig, schema);
      logger.warn(`Migrated config for mod '${id}' from YAML to database`);
    }
  }

  /**
   * Get combined Elysia app with all module routes mounted
   */
  getServer(): Elysia {
    // Mount each module's router under /{id} prefix
    for (const [id, loaded] of this.mods) {
      if (loaded.router) {
        const prefixed = new Elysia({ prefix: `/${id}` }).use(loaded.router);
        this.app.use(prefixed);
        logger.debug("Mounted router", { id, prefix: `/${id}` });
      }
    }

    return this.app;
  }

  /**
   * Get a loaded module by ID
   */
  get(id: string): Mod | undefined {
    return this.mods.get(id)?.mod;
  }

  /**
   * List all loaded module IDs
   */
  list(): string[] {
    return Array.from(this.mods.keys());
  }

  /**
   * Simple dependency sort (modules with no deps first)
   */
  private sortByDependencies(): string[] {
    // TODO: Implement proper topological sort for dependencies
    return Array.from(this.mods.keys());
  }
}
