import { defineConfig } from "@playwright/test";
import baseConfig from "../playwright.config";

/**
 * One-off wrapper so `screens-iphone-deep.spec.ts` (round-3 deep pass -- scroll-through of every
 * route below the fold + interactive states, see that file's header) can run without polluting
 * the main suite's testDir. Modelled on `pw.iphone-shots.config.ts` / `pw.tablet-shots.config.ts`.
 *
 * Usage:
 *
 *   cd e2e && npx playwright test --config=scripts/pw.iphone-deep.config.ts --project=iphone-safari
 *
 * Single project on purpose (iphone-safari = devices["iPhone 14"], WebKit -- what the user
 * actually sees over Tailscale); this pass does not need the Chromium cross-check project.
 */
export default defineConfig(baseConfig, {
  testDir: __dirname,
  testMatch: "screens-iphone-deep.spec.ts",
  reporter: [["list"]],
  timeout: 180_000, // section B's chat test alone budgets up to 150s
});
