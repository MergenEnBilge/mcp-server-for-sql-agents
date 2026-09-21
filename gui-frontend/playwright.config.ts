import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "@playwright/test";

// Sample-user passwords come from the repo's .env (never committed), or from the environment in CI.
try {
  for (const line of readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "../.env"), "utf-8").split(/\r?\n/)) {
    const match = /^([A-Z0-9_]+)=(.*)$/.exec(line);
    if (match && process.env[match[1]!] === undefined) process.env[match[1]!] = match[2];
  }
} catch {
  /* no .env: rely on the environment */
}

/**
 * End-to-end tests drive a real browser through a real Keycloak login against the running stack.
 * Point them at any running deployment with E2E_BASE_URL (default: the Vite dev server).
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    viewport: { width: 1360, height: 860 },
    ignoreHTTPSErrors: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
