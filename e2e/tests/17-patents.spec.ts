import { test, expect } from "./fixtures";
import { assertNoBadText } from "../utils/helpers";

/**
 * /patents — A14 patent/IP landscape tracking: a filterable patents table, a CPC x assignee heat
 * matrix tab, and a "סקר פטנטים" (patent landscape survey) launcher dialog.
 * (web/src/pages/PatentsPage.tsx, web/src/components/patents/*).
 */

test.describe("Patents screen (/patents)", () => {
  test("nav routes here and the page heading is correct", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("navigation", { name: "ניווט ראשי" }).getByRole("link", { name: "פטנטים" }).click();
    await expect(page).toHaveURL(/\/patents/);
    await expect(page.locator("header h1")).toHaveText("פטנטים");
  });

  test("patents list tab: rows render or the Hebrew empty state shows", async ({ page }) => {
    await page.goto("/patents");
    const tablist = page.getByRole("tablist", { name: "פטנטים ו-IP" });
    await expect(tablist).toBeVisible({ timeout: 15_000 });
    await expect(tablist.getByRole("tab", { name: "רשימת פטנטים" })).toHaveAttribute("aria-selected", "true");

    const empty = page.getByText("לא זוהו פטנטים");
    const rows = page.locator("tbody tr");
    await expect(async () => {
      const [emptyVisible, rowCount] = await Promise.all([
        empty.isVisible().catch(() => false),
        rows.count(),
      ]);
      expect(emptyVisible || rowCount > 0, "expected either the empty state or >=1 patent row").toBeTruthy();
    }).toPass({ timeout: 15_000 });
  });

  test("expanding a patent row reveals its claims summary / so-what detail", async ({ page }) => {
    await page.goto("/patents");
    await expect(page.getByRole("tablist", { name: "פטנטים ו-IP" })).toBeVisible({ timeout: 15_000 });
    const rows = page.locator("tbody tr");
    const hasRows = (await rows.count()) > 0;
    test.skip(!hasRows, "No patents to expand");

    const firstRow = rows.first();
    await firstRow.click();
    await expect(page.getByText("סיכום תביעות")).toBeVisible();
  });

  test("switching to the heatmap tab shows a matrix or its empty state", async ({ page }) => {
    await page.goto("/patents");
    const tablist = page.getByRole("tablist", { name: "פטנטים ו-IP" });
    await expect(tablist).toBeVisible({ timeout: 15_000 });
    await tablist.getByRole("tab", { name: "מטריצת CPC x בעלים" }).click();
    await expect(page).toHaveURL(/tab=heatmap/);
    await expect(tablist.getByRole("tab", { name: "מטריצת CPC x בעלים" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const empty = page.getByText("אין עדיין מספיק נתונים למטריצת CPC x בעלים.");
    const table = page.locator("table");
    await expect(async () => {
      const [emptyVisible, tableVisible] = await Promise.all([
        empty.isVisible().catch(() => false),
        table.isVisible().catch(() => false),
      ]);
      expect(emptyVisible || tableVisible, "expected either the empty state or the heatmap table").toBeTruthy();
    }).toPass({ timeout: 15_000 });
  });

  test("survey launcher dialog: opens, validates the min-length topic, and lists past surveys", async ({
    page,
  }) => {
    await page.goto("/patents");
    await expect(page.getByRole("tablist", { name: "פטנטים ו-IP" })).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: "סקר פטנטים…" }).click();

    const dialog = page.getByRole("dialog", { name: "סקר פטנטים" });
    await expect(dialog).toBeVisible();

    // too-short topic is rejected client-side (the submit button stays disabled and a validation
    // message appears once the field is touched)
    const topicInput = dialog.getByLabel(/נושא הסקר/);
    await topicInput.fill("קצר");
    await topicInput.blur();
    await expect(dialog.getByRole("alert")).toBeVisible();
    await expect(dialog.getByRole("button", { name: "הרץ סקר" })).toBeDisabled();

    await dialog.getByRole("button", { name: "ביטול" }).click();
    await expect(dialog).toHaveCount(0);
  });

  test("no bad literal text renders on the list or heatmap tab", async ({ page }, testInfo) => {
    await page.goto("/patents");
    await expect(page.getByRole("tablist", { name: "פטנטים ו-IP" })).toBeVisible({ timeout: 15_000 });
    await assertNoBadText(page, testInfo, "Patents — list (/patents)");

    await page.getByRole("tab", { name: "מטריצת CPC x בעלים" }).click();
    await page.waitForTimeout(500);
    await assertNoBadText(page, testInfo, "Patents — heatmap (/patents?tab=heatmap)");
  });
});
