/**
 * RTK Query client generation from the API's OpenAPI document (`pnpm generate:api`).
 *
 * Reads `openapi.json`, which `backend/scripts/export_openapi.py` writes, and writes
 * `generated.ts`: typed endpoints and hooks injected into `baseApi`. Each operation's OpenAPI
 * tags become its cache tags. Paths are relative to the package directory, where the script
 * runs.
 */
import { generateEndpoints, parseConfig } from "@rtk-query/codegen-openapi";
import type { ConfigFile } from "@rtk-query/codegen-openapi";

const config: ConfigFile = {
  schemaFile: "./src/api/openapi.json",
  apiFile: "./src/api/baseApi.ts",
  apiImport: "baseApi",
  outputFile: "./src/api/generated.ts",
  exportName: "api",
  hooks: true,
  tag: true,
};

for (const options of parseConfig(config)) {
  await generateEndpoints(options);
}
