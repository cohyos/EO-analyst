import { test, expect } from "./fixtures";
import { assertNoBadText } from "../utils/helpers";

test.describe("Conferences screen (/conferences)", () => {
  test("table has at least 10 rows", async ({ page }) => {
    await page.goto("/conferences");
    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible({ timeout: 15_000 });
    // rows include both the always-present summary row and, when expanded,
    // an extra detail row per conference — count only the clickable summary
    // rows (they carry aria-expanded).
    const summaryRows = page.locator("tbody tr[aria-expanded]");
    const n = await summaryRows.count();
    expect(n).toBeGreaterThanOrEqual(10);
  });

  test('each row has an outbound link (href http) or a clearly labeled "אין קישור" — never a silent blank', async ({
    page,
  }) => {
    await page.goto("/conferences");
    const summaryRows = page.locator("tbody tr[aria-expanded]");
    await expect(summaryRows.first()).toBeVisible({ timeout: 15_000 });
    const n = await summaryRows.count();

    const problems: string[] = [];
    for (let i = 0; i < n; i++) {
      const row = summaryRows.nth(i);
      const nameCell = row.locator("td").nth(1);
      const link = nameCell.locator("a");
      const hasLink = (await link.count()) > 0;
      if (hasLink) {
        const href = await link.getAttribute("href");
        if (!href || !/^https?:\/\//.test(href)) {
          problems.push(`row ${i}: link present but href is not http(s): "${href}"`);
        }
        continue;
      }
      const noLinkLabel = row.getByText("אין קישור");
      if ((await noLinkLabel.count()) === 0) {
        const name = (await nameCell.innerText()).trim();
        problems.push(`row ${i} ("${name}"): no outbound link AND no "אין קישור" label`);
      }
    }

    expect(problems, problems.join("\n")).toEqual([]);
  });

  test("clicking a row expands details; clicking again collapses", async ({ page }) => {
    await page.goto("/conferences");
    const firstRow = page.locator("tbody tr[aria-expanded]").first();
    await expect(firstRow).toBeVisible({ timeout: 15_000 });

    await expect(firstRow).toHaveAttribute("aria-expanded", "false");
    await firstRow.click();
    await expect(firstRow).toHaveAttribute("aria-expanded", "true");
    await firstRow.click();
    await expect(firstRow).toHaveAttribute("aria-expanded", "false");
  });

  test("iCal export (top-level) downloads text/calendar", async ({ page }) => {
    await page.goto("/conferences");
    await expect(page.locator("tbody tr").first()).toBeVisible({ timeout: 15_000 });

    const exportLink = page.getByRole("link", { name: /ייצוא iCal/ });
    await expect(exportLink).toBeVisible();

    const [response] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/conferences/ical")),
      exportLink.click(),
    ]);
    expect(response.status()).toBe(200);
    expect(response.headers()["content-type"] ?? "").toContain("text/calendar");
  });

  test('per-row "הוסף ליומן" triggers a real .ics file download', async ({ page }) => {
    await page.goto("/conferences");
    const firstRow = page.locator("tbody tr[aria-expanded]").first();
    await expect(firstRow).toBeVisible({ timeout: 15_000 });

    const addToCalBtn = firstRow.getByTitle("הוסף ליומן (ICS)");
    const [download] = await Promise.all([
      page.waitForEvent("download", { timeout: 10_000 }),
      addToCalBtn.click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/\.ics$/);
  });

  test("no bad literal text on the conferences screen", async ({ page }, testInfo) => {
    await page.goto("/conferences");
    await expect(page.locator("tbody tr").first()).toBeVisible({ timeout: 15_000 });
    await assertNoBadText(page, testInfo, "Conferences (/conferences)");
  });
});
