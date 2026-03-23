/**
 * Echo Module
 * 
 * Example built-in module that demonstrates the anima-mod interface.
 * Echoes back any message sent to it via HTTP.
 */

import { Elysia, t } from "elysia";
import type { Mod, ModContext } from "../../src/core/types.js";

export default {
  id: "echo",
  version: "1.0.0",

  configSchema: {
    prefix: { type: "string", label: "Echo Prefix", default: "echo:", description: "Prefix prepended to echoed messages" },
  },

  async init(ctx: ModContext) {
    ctx.logger.info("Echo module initialized", { 
      prefix: ctx.config.prefix ?? "Echo:" 
    });
  },

  getRouter() {
    return new Elysia()
      .get("/", () => ({ 
        module: "echo",
        description: "Echo module - POST /echo to echo a message back"
      }))
      
      .post("/echo", async ({ body }) => {
        return {
          echoed: body.message,
          count: body.count,
          timestamp: new Date().toISOString(),
        };
      }, {
        body: t.Object({ 
          message: t.String(), 
          count: t.Number({ default: 1 }) 
        })
      });
  },

  async start() {
    console.log("[echo] Module started");
  },

  async stop() {
    console.log("[echo] Module stopped");
  }
} satisfies Mod;
