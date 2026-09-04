import { test, expect } from "./fixtures";
import { assertNoHorizontalScroll } from "../utils/helpers";

const SCREENS = ["/", "/feed", "/entities", "/investigations", "/ask", "/conferences", "/inbox", "/reports", "/settings"];

test.describe("Theme toggle", () => {
  test("toggling switches data-theme and persists across a reload", async ({ page }) => {
    await page.goto("/");
    const html = page.locator("html");
    const initial = await html.getAttribute("data-theme");

    const toggleBtn = page.getByRole("button", { name: /עבור לערכת נושא/ });
    await toggleBtn.click();
    await expect(html).not.toHaveAttribute("data-theme", initial ?? "");

    const afterToggle = await html.getAttribute("data-theme");
    await page.reload();
    await expect(html).toHaveAttribute("data-theme", afterToggle ?? "");

    // restore original theme so other tests/users see the default
    if (afterToggle !== initial) {
      await page.getByRole("button", { name: /עבור לערכת נושא/ }).click();
    }
  });
});

test.describe("RTL correctness", () => {
  test("document.dir is rtl on every screen", async ({ page }) => {
    for (const path of SCREENS) {
      await page.goto(path);
      const dir = await page.evaluate(() => document.dir);
      expect(dir, `document.dir on ${path}`).toBe("rtl");
    }
  });
});

test.describe("No horizontal page scroll", () => {
  for (const path of SCREENS) {
    test(`no horizontal scroll on ${path}`, async ({ page }, testInfo) => {
      await page.goto(path);
      await page.waitForTimeout(1000); // allow data to load and layout to settle
      await assertNoHorizontalScroll(page, testInfo, `${path} (${testInfo.project.name})`);
    });
  }
});
