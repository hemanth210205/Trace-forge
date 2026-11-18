import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));

const PYTHON = path.resolve(HERE, "..", ".venv", "Scripts", "python.exe");

// End-to-end run against the real stack: the backend DEV server on traceforge_test (never the real
// database) and the Vite dev server. Uses the system Microsoft Edge, so no browser download is needed.
export default defineConfig({
  testDir: "./e2e",
  // Needs the seeded QA database and account; run it with playwright.qa.config.ts instead.
  testIgnore: "qa_walkthrough.spec.ts",
  timeout: 90_000,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: "http://localhost:5173", channel: "msedge", viewport: { width: 1440, height: 900 } },
  webServer: [
    {
      command: `"${PYTHON}" -m app.dev_test_server --port 8000`,
      cwd: "../backend",
      url: "http://127.0.0.1:8000/api/v1/health",
      reuseExistingServer: false, // never attach to a server that might be on the real database
      timeout: 60_000,
    },
    { command: "npm run dev", url: "http://localhost:5173", reuseExistingServer: true, timeout: 60_000 },
  ],
});
