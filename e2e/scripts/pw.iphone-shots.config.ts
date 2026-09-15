import { defineConfig } from "@playwright/test";
import baseConfig from "../playwright.config";

/**
 * One-off wrapper so `screens-iphone.spec.ts` (deliberately kept outside e2e/tests/ -- it's a
 * screenshot/readability capture pass, not a pass/fail QA test, see that file's header) can be
 * run without polluting the main suite's testDir. Modelled on `pw.tablet-shots.config.ts`.
 *
 * Usage:
 *
 *   cd e2e && npx playwright test --config=scripts/pw.iphone-shots.config.ts \
 *     --project=iphone-safari --project=mobile-390x844
 *
 * Both projects already exist in ../playwright.config.ts: "iphone-safari" (devices["iPhone 14"],
 * WebKit -- what the user actually sees over Tailscale) and "mobile-390x844" (Desktop Chrome
 * engine, viewport 390x844, isMobile/hasTouch -- Chromium cross-check).
 */
export default defineConfig(baseConfig, {
  testDir: __dirname,
  testMatch: "screens-iphone.spec.ts",
  reporter: [["list"]],
});
