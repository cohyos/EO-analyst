import { test, expect } from "./fixtures";
import { assertNoBadText } from "../utils/helpers";

const API_BASE = process.env.EOA_BASE_URL ?? "http://127.0.0.1:8765";

test.describe("Business development screen (/bd) -- A11", () => {
  test("header renders and the territory selector is populated", async ({ page }) => {
    await page.goto("/bd");
    await expect(page.locator("header h1")).toHaveText("פיתוח עסקי");

    const select = page.locator("#bd-territory");
    await expect(select).toBeVisible();
    // At least the placeholder option is always present; real/mock territories load async.
    await expect(async () => {
      const optionCount = await select.locator("option").count();
      expect(optionCount).toBeGreaterThan(0);
    }).toPass({ timeout: 15_000 });
  });

  test("selecting a territory shows the lookback selector and create-report button", async ({ page }) => {
    await page.goto("/bd");
    const select = page.locator("#bd-territory");
    await expect(async () => {
      expect(await select.locator("option").count()).toBeGreaterThan(1);
    }).toPass({ timeout: 15_000 });

    const firstRealValue = await select.locator("option").nth(1).getAttribute("value");
    test.skip(!firstRealValue, "No territories with activity in this environment");

    await select.selectOption(firstRealValue!);
    await expect(page.locator("#bd-lookback")).toBeVisible();
    await expect(page.getByRole("button", { name: "צור דוח" })).toBeVisible();
  });

  test("selecting an existing bd_territory report (if any) renders the report body and download links respond 200", async ({
    page,
    request,
  }) => {
    const reports = await (await request.get(`${API_BASE}/api/bd/reports?limit=30`)).json();
    test.skip(!Array.isArray(reports) || reports.length === 0, "No BD-by-territory reports exist in this environment");

    const report = reports[0];
    await page.goto(`/bd?territory=${report.territory}&id=${report.id}`);

    const article = page.locator(".report-body");
    await expect(article).toBeVisible({ timeout: 15_000 });

    const docxLink = page.getByRole("link", { name: "docx" });
    const mdLink = page.getByRole("link", { name: "md" });
    await expect(docxLink).toBeVisible();
    await expect(mdLink).toBeVisible();

    for (const link of [docxLink, mdLink]) {
      const href = await link.getAttribute("href");
      expect(href).toBeTruthy();
      const res = await request.get(new URL(href!, page.url()).toString());
      expect(res.status(), `${href} should respond 200`).toBe(200);
    }
  });

  test("no bad literal text on the bd screen", async ({ page }, testInfo) => {
    await page.goto("/bd");
    await expect(page.locator("header h1")).toHaveText("פיתוח עסקי");
    await assertNoBadText(page, testInfo, "Business development (/bd)");
  });
});
