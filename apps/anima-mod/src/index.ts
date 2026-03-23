/**
 * anima-mod: Entry Point
 * 
 * Anima Module System - The external presence layer.
 */

import { createServer } from "./server.js";
import { loadConfig } from "./core/config.js";
import { createLogger } from "./core/logger.js";

const logger = createLogger("a-mod");

async function main() {
  logger.info("Starting anima-mod...");

  // Load configuration
  const config = await loadConfig();
  const port = config.core?.port ?? 3034;
  const hostname = config.core?.hostname ?? "127.0.0.1";

  // Create and start server
  const app = await createServer({ port, hostname });

  app.listen({ port, hostname }, () => {
    logger.info(`anima-mod listening on http://${hostname}:${port}`);
  });
}

main().catch((err) => {
  logger.error("Fatal error", { error: err.message });
  process.exit(1);
});

export type { App } from "./server.js";
