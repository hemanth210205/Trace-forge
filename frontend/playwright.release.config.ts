import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PYTHON = path.resolve(HERE, "..", ".venv", "Scripts", "python.exe");

// The documented local-release startup, exactly: build the console, then `python -m app.serve`, which serves the
// API and the built console from one origin. Only the database differs (traceforge_test instead of the default
// traceforge_dev, so the run leaves no data behind) and the port (8001, so it never meets a dev server on 8000).
export default defineConfig({
  testDir: "./e2e-release",
  timeout: 90_000,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: "http://127.0.0.1:8001", channel: "msedge", viewport: { width: 1440, height: 900 } },
  webServer: {
    command: `npm run build && cd ../backend && "${PYTHON}" -m app.serve --database test --port 8001`,
    url: "http://127.0.0.1:8001/api/v1/health",
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
