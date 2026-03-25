/**
 * Generate TypeScript types from FastAPI's OpenAPI schema.
 *
 * Usage:
 *   bun run packages/api-client/scripts/generate-types.ts [url]
 *
 * Default URL: http://localhost:8000/openapi.json
 *
 * The generated file is written to packages/api-client/src/generated.ts.
 * It is NOT auto-imported — compare with types.ts and adopt manually.
 */

const url = process.argv[2] ?? "http://localhost:8000/openapi.json";

async function main() {
  console.log(`Fetching OpenAPI schema from ${url}...`);

  const res = await fetch(url);
  if (!res.ok) {
    console.error(`Failed to fetch schema: ${res.status} ${res.statusText}`);
    console.error("Is the server running? Start it with: bun run dev:server");
    process.exit(1);
  }

  const schema = await res.json();
  const output = Bun.resolveSync("../src/generated.ts", import.meta.dir);

  // Use openapi-typescript if available, otherwise write raw schema
  try {
    const openapiTS = await import("openapi-typescript");
    const ts = await openapiTS.default(schema);
    await Bun.write(output, ts);
    console.log(`Generated types written to ${output}`);
  } catch {
    // Fallback: just save the schema as JSON for manual comparison
    const fallback = Bun.resolveSync("../src/openapi-schema.json", import.meta.dir);
    await Bun.write(fallback, JSON.stringify(schema, null, 2));
    console.log(`openapi-typescript not installed. Schema saved to ${fallback}`);
    console.log("Install it with: bun add -d openapi-typescript");
  }
}

main();
