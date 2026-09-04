import { test, expect } from "./fixtures";
import { assertNoBadText } from "../utils/helpers";

test.describe("Entities screens (/entities, /entities/:id)", () => {
  test('searching "Elbit" returns matching rows', async ({ page }) => {
    await page.goto("/entities");
    await page.getByLabel("חיפוש ישויות").fill("Elbit");
    await page.waitForTimeout(600);

    const list = page.getByRole("list");
    await expect(list).toBeVisible({ timeout: 15_000 });
    const items = list.locator("li");
    await expect(items.first()).toBeVisible();
    const count = await items.count();
    expect(count).toBeGreaterThan(0);

    const text = (await items.first().innerText()).toLowerCase();
    expect(text).toContain("elbit");
  });

  test("entity detail page shows a timeline section and a Cytoscape graph canvas", async ({ page }) => {
    await page.goto("/entities");
    await page.getByLabel("חיפוש ישויות").fill("Elbit");
    await page.waitForTimeout(600);

    const firstLink = page.getByRole("list").locator("li a").first();
    await expect(firstLink).toBeVisible({ timeout: 15_000 });
    await firstLink.click();
    await expect(page).toHaveURL(/\/entities\/\d+/);

    const timelineSection = page.locator("section[aria-label='ציר זמן']");
    await expect(timelineSection).toBeVisible({ timeout: 15_000 });

    const graphSection = page.locator("section[aria-label='גרף ישויות']");
    await expect(graphSection).toBeVisible();
    const canvas = graphSection.locator("canvas");
    await expect(canvas.first()).toBeVisible({ timeout: 15_000 });
  });

  test("named-query buttons respond without an error toast/alert", async ({ page, consoleErrors }, testInfo) => {
    await page.goto("/entities");
    await page.getByLabel("חיפוש ישויות").fill("Elbit");
    await page.waitForTimeout(600);
    const firstLink = page.getByRole("list").locator("li a").first();
    await expect(firstLink).toBeVisible({ timeout: 15_000 });
    await firstLink.click();
    await expect(page.locator("section[aria-label='גרף ישויות']")).toBeVisible({ timeout: 15_000 });

    const namedQueryButtons = [
      "שותפי המתחרים",
      "ספקי המתמודדים בתוכנית",
      "סטארטאפים מחוברים",
    ];

    for (const label of namedQueryButtons) {
      const btn = page.getByRole("button", { name: label });
      await expect(btn).toBeVisible();
      await btn.click();
      await page.waitForTimeout(500);
      await expect(page.getByRole("alert")).toHaveCount(0);
    }

    // Named-query result summary must render a real result count, not a
    // crash/blank.
    const resultLine = page.getByText(/תוצאות$/);
    await expect(resultLine).toBeVisible({ timeout: 10_000 });

    void testInfo;
    void consoleErrors;
  });

  test("no bad literal text on the entities list or an entity detail page", async ({ page }, testInfo) => {
    await page.goto("/entities");
    await assertNoBadText(page, testInfo, "Entities list (/entities)");

    await page.getByLabel("חיפוש ישויות").fill("Elbit");
    await page.waitForTimeout(600);
    const firstLink = page.getByRole("list").locator("li a").first();
    await expect(firstLink).toBeVisible({ timeout: 15_000 });
    await firstLink.click();
    await expect(page.locator("section[aria-label='גרף ישויות']")).toBeVisible({ timeout: 15_000 });
    await assertNoBadText(page, testInfo, "Entity detail (/entities/:id)");
  });
});
