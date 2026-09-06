import { defineConfig, devices } from "@playwright/test";

/**
 * EO-Analyst UI quality suite.
 *
 * Runs against the LIVE app + real backend at BASE_URL (default
 * http://127.0.0.1:8765). No webServer block on purpose — this suite never
 * starts, builds, or proxies the web app; it drives whatever is already
 * running (the current build, real Postgres data: ~250 items, ~40+
 * entities, conferences, investigations, surveys). If nothing answers at
 * BASE_URL the whole run fails fast with ECONNREFUSED, which is the
 * intended signal ("start the app first"), not something this config
 * should paper over.
 */
// Precedence: PW_BASE_URL / BASE_URL (generic Playwright-style overrides,
// used to point this suite at a locally-running `npm run preview`/`npm run
// dev` build instead of the live container) win over the suite's own
// EOA_BASE_URL, which wins over the default.
const BASE_URL =
  process.env.PW_BASE_URL ?? process.env.BASE_URL ?? process.env.EOA_BASE_URL ?? "http://127.0.0.1:8765";

export default defineConfig({
  testDir: "./tests",
  timeout: 150_000, // the Ask-the-analyst flow is allowed up to 120s by spec
  expect: { timeout: 15_000 },
  fullyParallel: false, // shared live backend / shared data — avoid cross-test interference
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  globalSetup: require.resolve("./utils/global-setup"),
  reporter: [
    ["html", { outputFolder: "report", open: "never" }],
    ["list"],
    ["./utils/qa-findings-reporter"],
  ],
  outputDir: "test-results",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  projects: [
    {
      name: "desktop-1440x900",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: "mobile-390x844",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      },
    },
    // iPad Air (gen 7-ish) portrait, WebKit/Safari engine — closest match to
    // what the analyst will actually see on a real iPad over Tailscale.
    {
      name: "tablet-820x1180",
      use: {
        ...devices["iPad (gen 7)"],
        viewport: { width: 820, height: 1180 },
        screen: { width: 820, height: 1180 },
      },
    },
    // Same device, landscape. 1180px still sits inside the explicit tablet
    // band (768-1279px) the responsive rework targets — desktop-only 3-pane/
    // expanded-rail layouts key off `xl` (1280px), not `lg` (1024px), so this
    // project exercises the "wide tablet" 2-column layouts, not the desktop
    // ones, even though 1180 > 1024.
    {
      name: "tablet-landscape-1180x820",
      use: {
        ...devices["iPad (gen 7)"],
        viewport: { width: 1180, height: 820 },
        screen: { width: 1180, height: 820 },
      },
    },
    // iPhone 14, WebKit/Safari engine.
    {
      name: "iphone-safari",
      use: {
        ...devices["iPhone 14"],
      },
    },
  ],
});
