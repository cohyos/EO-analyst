import { defineConfig } from "@playwright/test";
import baseConfig from "../playwright.config";

/**
 * One-off wrapper so `screens-tablet.spec.ts` (deliberately kept outside
 * e2e/tests/ — it's a screenshot capture pass, not a pass/fail QA test, see
 * that file's header) can be run without polluting the main suite's
 * testDir. Usage:
 *
 *   npx playwright test --config=scripts/pw.tablet-shots.config.ts \
 *     --project=tablet-820x1180 --project=tablet-landscape-1180x820 --project=iphone-safari
 */
export default defineConfig(baseConfig, {
  testDir: __dirname,
  testMatch: "screens-tablet.spec.ts",
  reporter: [["list"]],
});
