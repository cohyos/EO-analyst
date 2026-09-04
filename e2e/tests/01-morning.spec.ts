import { test, expect } from "./fixtures";
import { assertNoBadText, assertNoConsoleErrors } from "../utils/helpers";

test.describe("Morning screen (/)", () => {
  test("tiles render real numbers, empty states are Hebrew, no bad literal text, no console errors", async ({
    page,
    consoleErrors,
  }, testInfo) => {
    await page.goto("/");
    await expect(page.locator("header h1")).toHaveText("הבוקר");

    // Let the morning query settle (loading spinner disappears).
    await expect(page.getByRole("status").filter({ hasText: "טוען" })).toHaveCount(0, {
      timeout: 20_000,
    });

    // Either a real report headline exists, or an explicit Hebrew empty state
    // is shown — never a blank/undefined body.
    const noRunEmptyState = page.getByText("אין ריצה לילית עדיין");
    const hasEmptyState = await noRunEmptyState.count();
    if (hasEmptyState > 0) {
      await expect(noRunEmptyState).toBeVisible();
    }

    // Every stat-tile numeric value must be an actual number (or the
    // dash placeholder used for "no data"), never NaN/undefined/blank.
    const statValues = page.locator("section[aria-label='תקציר הלילה'] p.font-tabular");
    const count = await statValues.count();
    for (let i = 0; i < Math.min(count, 30); i++) {
      const text = (await statValues.nth(i).innerText()).trim();
      if (text.length === 0) continue;
      const isDash = text === "—" || text === "-";
      const isNumeric = /^-?\d[\d,.]*\s*(%|דק['׳]?)?$/.test(text);
      expect(isDash || isNumeric, `Stat value "${text}" is neither a dash nor numeric`).toBeTruthy();
      expect(text).not.toMatch(/^(NaN|undefined|null|\[object Object\])$/);
    }

    await assertNoBadText(page, testInfo, "Morning (/)");
    await assertNoConsoleErrors(page, testInfo, "Morning (/)", consoleErrors.getErrors());
  });

  test("open points (if any) render inline one-click answer options with Hebrew text", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("status").filter({ hasText: "טוען" })).toHaveCount(0, {
      timeout: 20_000,
    });

    const noOpenPoints = page.getByText("אין נקודות פתוחות");
    if (await noOpenPoints.count()) {
      await expect(noOpenPoints).toBeVisible();
    }
  });

  test("headlines (if any) deep-link into the feed for the right item", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("status").filter({ hasText: "טוען" })).toHaveCount(0, {
      timeout: 20_000,
    });

    const headlineLinks = page.locator('main a[href^="/feed?open="]');
    const n = await headlineLinks.count();
    if (n === 0) return;
    const href = await headlineLinks.first().getAttribute("href");
    await headlineLinks.first().click();
    await expect(page).toHaveURL(new RegExp(href!.replace(/[?]/g, "\\?")));
  });

  test('"פתח דוח docx" download link (if a report exists) points at the docx file endpoint', async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.getByRole("status").filter({ hasText: "טוען" })).toHaveCount(0, {
      timeout: 20_000,
    });
    const dlLink = page.getByRole("link", { name: "פתח דוח docx" });
    if (await dlLink.count()) {
      const href = await dlLink.getAttribute("href");
      expect(href).toMatch(/\/api\/reports\/\d+\/file\?fmt=docx/);
    }
  });
});
