import { test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * One-off screenshot pass for the iOS/tablet responsiveness rework (not part
 * of the pass/fail QA suite — lives outside e2e/tests on purpose so a normal
 * `npx playwright test` run doesn't pick it up). Run explicitly with e.g.:
 *
 *   npx playwright test --config=playwright.config.ts scripts/screens-tablet.spec.ts \
 *     --project=tablet-820x1180 --project=tablet-landscape-1180x820 --project=iphone-safari
 *
 * Saves docs/qa/screens_tablet/<screen>_<project>_<theme>.png, mirroring the
 * naming convention of the earlier docs/qa/screens_r2 pass.
 */

const OUT_DIR = path.join(__dirname, "..", "..", "docs", "qa", "screens_tablet");

const SCREENS: { name: string; path: string; waitMs?: number }[] = [
  { name: "morning", path: "/" },
  { name: "feed", path: "/feed" },
  { name: "feed_detail_open", path: "/feed", waitMs: 800 },
  { name: "entities_list", path: "/entities" },
  { name: "conferences", path: "/conferences" },
  { name: "reports_list", path: "/reports" },
  { name: "settings", path: "/settings" },
];

async function setTheme(page: import("@playwright/test").Page, theme: "dark" | "light") {
  // Matches the zustand `persist` shape uiStore.ts writes under the key
  // "eo-analyst-ui" (partialize: { theme, locale }) — a plain
  // `document.documentElement.dataset.theme` write alone gets clobbered by
  // AppShell re-applying the store's own theme on mount/reload.
  await page.evaluate((t) => {
    try {
      const raw = localStorage.getItem("eo-analyst-ui");
      const parsed = raw ? JSON.parse(raw) : { state: {}, version: 0 };
      parsed.state = { ...parsed.state, theme: t, locale: parsed.state?.locale ?? "he" };
      localStorage.setItem("eo-analyst-ui", JSON.stringify(parsed));
    } catch {
      /* best-effort */
    }
    document.documentElement.dataset.theme = t;
  }, theme);
}

test.describe("Tablet/iOS screenshot pass", () => {
  for (const theme of ["dark", "light"] as const) {
    for (const screen of SCREENS) {
      test(`${screen.name} — ${theme}`, async ({ page }, testInfo) => {
        fs.mkdirSync(OUT_DIR, { recursive: true });
        await page.goto(screen.path);
        await setTheme(page, theme);
        await page.reload();
        await page.waitForTimeout(1200 + (screen.waitMs ?? 0));

        if (screen.name === "feed_detail_open") {
          // Open the first feed row's inline detail panel, if any items loaded.
          const firstRow = page.locator('[data-testid^="feed-row-"]').first();
          if (await firstRow.isVisible().catch(() => false)) {
            await firstRow.dblclick().catch(() => {});
            await page.waitForTimeout(600);
          }
        }

        const fileName = `${screen.name}_${testInfo.project.name}_${theme}.png`;
        await page.screenshot({ path: path.join(OUT_DIR, fileName), fullPage: false });
      });
    }
  }
});
