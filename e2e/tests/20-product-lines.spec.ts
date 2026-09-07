import { test, expect } from "./fixtures";
import { assertNoBadText } from "../utils/helpers";

const API_BASE = process.env.EOA_BASE_URL ?? "http://127.0.0.1:8765";

// PL-ui (2026-09-07): "קווי מוצר" -- product-line status & business-development tracking, built
// against the frozen contract in docs/qa/loop/round_7_fixes.md "### PL-ui status". The endpoints
// (`GET /api/product-lines`, `GET /api/product-lines/{id}`, `POST /api/product-lines/{id}/report`)
// may not exist on the live backend this suite runs against yet -- every test here checks the
// list endpoint first and `test.skip`s if it 404s, same pattern as `15-bd.spec.ts` skipping when
// no bd_territory reports exist.
test.describe("Product lines screen (/product-lines) -- PL-ui", () => {
  test("header renders", async ({ page }) => {
    await page.goto("/product-lines");
    await expect(page.locator("header h1")).toHaveText("קווי מוצר");
  });

  test("the nav rail links to /product-lines next to פיתוח עסקי", async ({ page }) => {
    await page.goto("/bd");
    const link = page.getByRole("link", { name: "קווי מוצר" });
    await expect(link).toBeVisible();
    await link.click();
    await expect(page).toHaveURL(/\/product-lines$/);
  });

  test("GET /api/product-lines responds and the page renders cards or the empty state, never a blank screen", async ({
    page,
    request,
  }) => {
    const res = await request.get(`${API_BASE}/api/product-lines`);
    test.skip(res.status() === 404, "GET /api/product-lines not implemented on this backend yet");
    expect(res.ok()).toBe(true);
    const body = await res.json();
    expect(Array.isArray(body)).toBe(true);

    await page.goto("/product-lines");
    const cards = page.locator('[data-testid^="product-line-card-"]');
    const empty = page.getByText("לא נמצאו קווי מוצר");
    await expect(async () => {
      const hasCards = (await cards.count()) > 0;
      const hasEmpty = (await empty.count()) > 0;
      expect(hasCards || hasEmpty).toBe(true);
    }).toPass({ timeout: 15_000 });
  });

  test("opening a product line's detail page shows its stat tiles and tabs", async ({
    page,
    request,
  }) => {
    const res = await request.get(`${API_BASE}/api/product-lines`);
    test.skip(res.status() === 404, "GET /api/product-lines not implemented on this backend yet");
    const body = await res.json();
    test.skip(!Array.isArray(body) || body.length === 0, "No product lines returned by this backend");

    const first = body[0];
    await page.goto(`/product-lines/${first.id}`);
    await expect(page.getByRole("tab", { name: /פריטים אחרונים/ })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("tab", { name: /מכרזים פתוחים/ })).toBeVisible();
    await expect(page.getByRole("tab", { name: /^דוחות/ })).toBeVisible();
  });

  test("the Feed page's 'קו מוצר' filter opens and lists all six product lines", async ({ page }) => {
    await page.goto("/feed");
    const toggle = page.getByTestId("product-line-filter-toggle");
    await expect(toggle).toBeVisible();
    await toggle.click();
    const menu = page.getByTestId("product-line-filter-menu");
    await expect(menu).toBeVisible();
    await expect(menu.locator("button")).toHaveCount(6);
  });

  test("the Tenders page's 'קו מוצר' filter opens and lists all six product lines", async ({ page }) => {
    await page.goto("/tenders");
    const toggle = page.getByTestId("product-line-filter-toggle");
    await expect(toggle).toBeVisible();
    await toggle.click();
    const menu = page.getByTestId("product-line-filter-menu");
    await expect(menu).toBeVisible();
    await expect(menu.locator("button")).toHaveCount(6);
  });

  test("no bad literal text on the product-lines screen", async ({ page }, testInfo) => {
    await page.goto("/product-lines");
    await expect(page.locator("header h1")).toHaveText("קווי מוצר");
    await assertNoBadText(page, testInfo, "Product lines (/product-lines)");
  });
});
