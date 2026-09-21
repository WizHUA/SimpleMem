import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  outputDir: "./integration-results",
  testMatch: "integration.spec.ts",
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:5176",
    viewport: { width: 1440, height: 960 },
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "python tests/backend_smoke_server.py",
      url: "http://127.0.0.1:8095/health",
      reuseExistingServer: false,
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 5176",
      url: "http://127.0.0.1:5176",
      env: { MEMORY_UI_PROXY_TARGET: "http://127.0.0.1:8095" },
      reuseExistingServer: false,
    },
  ],
});
