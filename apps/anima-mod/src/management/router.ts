import { Elysia } from "elysia";
import type { AnyElysia } from "elysia";
import type { ConfigService } from "./config-service.js";
import type { ModState, StateService } from "./state-service.js";
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

function isUserMod(path: string): boolean {
  return path.replace(/\\/g, "/").split("/").includes("user-mods");
}

export function createManagementRouter(deps: ManagementDeps): AnyElysia {
  const { mods, configService, stateService, eventService } = deps;

  const getStatePayload = async (id: string): Promise<ModState> => {
    return await stateService.getState(id) ?? {
      modId: id,
      enabled: false,
      status: "stopped",
      lastError: null,
      startedAt: null,
      updatedAt: null,
    };
  };

  return new Elysia()
    // CORS — scoped to management API only; won't touch WebSocket upgrades
    .onAfterHandle(({ set }) => {
      set.headers["access-control-allow-origin"] = "*";
      set.headers["access-control-allow-methods"] = "GET, POST, PUT, DELETE, PATCH, OPTIONS";
      set.headers["access-control-allow-headers"] = "content-type, authorization, x-anima-nonce";
      set.headers["access-control-allow-credentials"] = "true";
    })
    .options("/*", () => new Response(null, { status: 204 }))
    // List all mods
    .get("/api/mods", async () => {
      const result = [];
      for (const [id, loaded] of mods) {
        const state = await getStatePayload(id);
        result.push({
          id,
          version: loaded.mod?.version ?? "unknown",
          status: state.status,
          enabled: state.enabled,
          hasConfigSchema: !!loaded.mod?.configSchema,
          hasSetupGuide: !!(loaded.mod?.setupGuide && loaded.mod.setupGuide.length > 0),
          toolsCount: loaded.mod?.toolSchemas?.length ?? 0,
          canUninstall: isUserMod(loaded.config.path),
        });
      }
      return result;
    })

    // Get mod detail
    .get("/api/mods/:id", async ({ params }) => {
      const loaded = mods.get(params.id);
      if (!loaded?.mod) throw new Error(`Module ${params.id} not found`);

      const state = await getStatePayload(params.id);
      const config = await configService.getConfig(params.id, { maskSecrets: true });
      const events = await eventService.getEvents(params.id, 20);

      return {
        id: params.id,
        version: loaded.mod.version,
        status: state.status,
        enabled: state.enabled,
        configSchema: loaded.mod.configSchema ?? null,
        setupGuide: loaded.mod.setupGuide ?? null,
        config,
        toolsCount: loaded.mod.toolSchemas?.length ?? 0,
        canUninstall: isUserMod(loaded.config.path),
        events,
        health: {
          status: state.status,
          uptime: state.startedAt,
          lastError: state.lastError,
        },
      };
    })

    // Enable mod
    .post("/api/mods/:id/enable", async ({ params }) => {
      if (!mods.has(params.id)) throw new Error(`Module ${params.id} not found`);
      if (deps.onEnable) {
        await deps.onEnable(params.id);
      } else {
        await stateService.setState(params.id, {
          enabled: true,
          status: "running",
          startedAt: new Date().toISOString(),
          lastError: null,
        });
        await eventService.logEvent(params.id, "started");
      }
      const state = await getStatePayload(params.id);
      broadcastModEvent({ type: "mod:status", modId: params.id, status: state.status });
      return state;
    })

    // Disable mod
    .post("/api/mods/:id/disable", async ({ params }) => {
      if (!mods.has(params.id)) throw new Error(`Module ${params.id} not found`);
      if (deps.onDisable) {
        await deps.onDisable(params.id);
      } else {
        await stateService.setState(params.id, { enabled: false, status: "stopped" });
        await eventService.logEvent(params.id, "stopped");
      }
      const state = await getStatePayload(params.id);
      broadcastModEvent({ type: "mod:status", modId: params.id, status: state.status });
      return state;
    })

    // Restart mod
    .post("/api/mods/:id/restart", async ({ params }) => {
      if (!mods.has(params.id)) throw new Error(`Module ${params.id} not found`);
      if (deps.onRestart) {
        await deps.onRestart(params.id);
      } else {
        await stateService.setState(params.id, {
          enabled: true,
          status: "running",
          startedAt: new Date().toISOString(),
          lastError: null,
        });
        await eventService.logEvent(params.id, "started");
      }
      const state = await getStatePayload(params.id);
      broadcastModEvent({ type: "mod:status", modId: params.id, status: state.status });
      return state;
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
      const configKeys = Object.keys(body as Record<string, unknown>).sort();
      await eventService.logEvent(params.id, "config_changed", { fields: configKeys });

      // Restart if running
      const state = await stateService.getState(params.id);
      if (state?.status === "running" && deps.onRestart) {
        await deps.onRestart(params.id);
        broadcastModEvent({ type: "mod:status", modId: params.id, status: "running" });
      }

      return { status: "updated", state: await getStatePayload(params.id) };
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

      return { status: "uninstalled", id: params.id };
    })

    // Health check
    .get("/api/mods/:id/health", async ({ params }) => {
      const state = await getStatePayload(params.id);
      return {
        status: state.status,
        uptime: state.startedAt,
        lastError: state.lastError,
      };
    })

    // Recent events
    .get("/api/mods/:id/events", async ({ params, query }) => {
      if (!mods.has(params.id)) throw new Error(`Module ${params.id} not found`);
      const requestedLimit = Number(query.limit ?? 20);
      const limit = Number.isFinite(requestedLimit)
        ? Math.max(1, Math.min(100, Math.floor(requestedLimit)))
        : 20;
      return eventService.getEvents(params.id, limit);
    })

    // Aggregate tool schemas from all running mods.
    // The cognitive core (Python) polls this to build @tool wrappers dynamically.
    .get("/api/tools", async () => {
      const tools = [];
      for (const [id, loaded] of mods) {
        if (!loaded.mod?.toolSchemas?.length) continue;
        const state = await stateService.getState(id);
        if (state?.status !== "running") continue;
        for (const schema of loaded.mod.toolSchemas) {
          tools.push({ ...schema, modId: id });
        }
      }
      return tools;
    });
}
