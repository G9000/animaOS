import { Elysia, t } from "elysia";
import type { ConfigService } from "./config-service.js";
import type { StateService } from "./state-service.js";
import type { EventService } from "./event-service.js";
import type { Mod, ModConfig } from "../core/types.js";
import { broadcastModEvent } from "./ws.js";
import { installMod, uninstallMod } from "./installer.js";

interface LoadedMod {
  config: ModConfig;
  mod?: Mod;
  ctx?: any;
  router?: any;
}

interface ManagementDeps {
  mods: Map<string, LoadedMod>;
  configService: ConfigService;
  stateService: StateService;
  eventService: EventService;
  onRestart?: (id: string) => Promise<void>;
  onEnable?: (id: string) => Promise<void>;
  onDisable?: (id: string) => Promise<void>;
}

export function createManagementRouter(deps: ManagementDeps): Elysia {
  const { mods, configService, stateService, eventService } = deps;

  return new Elysia()
    // List all mods
    .get("/api/mods", async () => {
      const result = [];
      for (const [id, loaded] of mods) {
        const state = await stateService.getState(id);
        result.push({
          id,
          version: loaded.mod?.version ?? "unknown",
          status: state?.status ?? "stopped",
          enabled: state?.enabled ?? false,
          hasConfigSchema: !!loaded.mod?.configSchema,
          hasSetupGuide: !!(loaded.mod?.setupGuide && loaded.mod.setupGuide.length > 0),
        });
      }
      return result;
    })

    // Get mod detail
    .get("/api/mods/:id", async ({ params }) => {
      const loaded = mods.get(params.id);
      if (!loaded?.mod) throw new Error(`Module ${params.id} not found`);

      const state = await stateService.getState(params.id);
      const config = await configService.getConfig(params.id, { maskSecrets: true });

      return {
        id: params.id,
        version: loaded.mod.version,
        status: state?.status ?? "stopped",
        enabled: state?.enabled ?? false,
        configSchema: loaded.mod.configSchema ?? null,
        setupGuide: loaded.mod.setupGuide ?? null,
        config,
        health: {
          status: state?.status ?? "stopped",
          uptime: state?.startedAt ?? null,
          lastError: state?.lastError ?? null,
        },
      };
    })

    // Enable mod
    .post("/api/mods/:id/enable", async ({ params }) => {
      if (!mods.has(params.id)) throw new Error(`Module ${params.id} not found`);
      if (deps.onEnable) await deps.onEnable(params.id);
      broadcastModEvent({ type: "mod:status", modId: params.id, status: "running" });
      return { status: "enabled" };
    })

    // Disable mod
    .post("/api/mods/:id/disable", async ({ params }) => {
      if (!mods.has(params.id)) throw new Error(`Module ${params.id} not found`);
      if (deps.onDisable) await deps.onDisable(params.id);
      broadcastModEvent({ type: "mod:status", modId: params.id, status: "stopped" });
      return { status: "disabled" };
    })

    // Restart mod
    .post("/api/mods/:id/restart", async ({ params }) => {
      if (!mods.has(params.id)) throw new Error(`Module ${params.id} not found`);
      if (deps.onRestart) await deps.onRestart(params.id);
      broadcastModEvent({ type: "mod:status", modId: params.id, status: "running" });
      return { status: "restarted" };
    })

    // Update config
    .put("/api/mods/:id/config", async ({ params, body }) => {
      const loaded = mods.get(params.id);
      if (!loaded?.mod) throw new Error(`Module ${params.id} not found`);

      await configService.setConfig(
        params.id,
        body as Record<string, unknown>,
        loaded.mod.configSchema
      );
      await eventService.logEvent(params.id, "config_changed", body as Record<string, unknown>);

      // Restart if running
      const state = await stateService.getState(params.id);
      if (state?.status === "running" && deps.onRestart) {
        await deps.onRestart(params.id);
        broadcastModEvent({ type: "mod:status", modId: params.id, status: "running" });
      }

      return { status: "updated" };
    })

    // Install mod from GitHub
    .post("/api/mods/install", async ({ body }) => {
      const { source } = body as { source: string };
      if (!source) throw new Error("Missing 'source' field");
      const result = await installMod(source);
      return result;
    })

    // Uninstall mod
    .post("/api/mods/:id/uninstall", async ({ params }) => {
      const loaded = mods.get(params.id);
      if (!loaded) throw new Error(`Module ${params.id} not found`);

      // Stop if running
      if (deps.onDisable) {
        try { await deps.onDisable(params.id); } catch { /* may not be running */ }
      }

      await uninstallMod(params.id, loaded.config.path);
      mods.delete(params.id);

      return { status: "uninstalled" };
    })

    // Health check
    .get("/api/mods/:id/health", async ({ params }) => {
      const state = await stateService.getState(params.id);
      return {
        status: state?.status ?? "stopped",
        uptime: state?.startedAt ?? null,
        lastError: state?.lastError ?? null,
      };
    });
}
