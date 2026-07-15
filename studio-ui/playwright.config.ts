import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repository = fileURLToPath(new URL("..", import.meta.url));
const port = Number(process.env.LEDGERLINE_E2E_PORT ?? "8876");
const localPython = path.join(repository, ".venv", "Scripts", "python.exe");
const python = process.env.LEDGERLINE_E2E_PYTHON
  ?? (process.platform === "win32" && existsSync(localPython) ? localPython : "python");
const pythonPath = [path.join(repository, "src"), process.env.PYTHONPATH]
  .filter(Boolean)
  .join(path.delimiter);

export default defineConfig({
  testDir: "./e2e",
  outputDir: path.join(repository, ".cache", "playwright-results"),
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 45_000,
  expect: { timeout: 8_000 },
  reporter: process.env.CI
    ? [["line"], ["html", { outputFolder: path.join(repository, ".cache", "playwright-report"), open: "never" }]]
    : "line",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    command: `"${python}" scripts/run_studio_e2e.py --port ${port}`,
    cwd: repository,
    env: {
      ...process.env,
      PYTHONPATH: pythonPath,
      PYTHONUNBUFFERED: "1",
    },
    url: `http://127.0.0.1:${port}/api/health`,
    timeout: 120_000,
    reuseExistingServer: false,
  },
});
