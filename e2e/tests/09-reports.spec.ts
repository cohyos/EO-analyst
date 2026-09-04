import { test, expect } from "./fixtures";
import { assertNoBadText } from "../utils/helpers";

const API_BASE = process.env.EOA_BASE_URL ?? "http://127.0.0.1:8765";

test.describe("Reports screen (/reports)", () => {
  test("list renders rows or an explicit empty state", async ({ page }) => {
    await page.goto("/reports");
    await expect(page.locator("header h1")).toHaveText("דוחות");

    const empty = page.getByText("אין דוחות");
    const rows = page.locator("ul li button");
    await expect(async () => {
      const [emptyVisible, rowCount] = await Promise.all([empty.isVisible().catch(() => false), rows.count()]);
      expect(emptyVisible || rowCount > 0).toBeTruthy();
    }).toPass({ timeout: 15_000 });
  });

  test("selecting a report (if any exist) renders the HTML viewer and the docx/md download links respond 200", async ({
    page,
    request,
  }) => {
    const reports = await (await request.get(`${API_BASE}/api/reports?limit=30`)).json();
    test.skip(!Array.isArray(reports) || reports.length === 0, "No reports exist in this environment");

    await page.goto(`/reports?id=${reports[0].id}`);
    const article = page.locator("article");
    await expect(article).toBeVisible({ timeout: 15_000 });

    const docxLink = article.getByRole("link", { name: "docx" });
    const mdLink = article.getByRole("link", { name: "md" });
    await expect(docxLink).toBeVisible();
    await expect(mdLink).toBeVisible();

    for (const link of [docxLink, mdLink]) {
      const href = await link.getAttribute("href");
      expect(href).toBeTruthy();
      const res = await request.get(new URL(href!, page.url()).toString());
      expect(res.status(), `${href} should respond 200`).toBe(200);
    }
  });

  test("no bad literal text on the reports screen", async ({ page }, testInfo) => {
    await page.goto("/reports");
    await expect(page.locator("header h1")).toHaveText("דוחות");
    await assertNoBadText(page, testInfo, "Reports (/reports)");
  });
});
