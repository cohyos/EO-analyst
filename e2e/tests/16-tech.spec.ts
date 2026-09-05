import { test, expect } from "./fixtures";
import { assertNoBadText } from "../utils/helpers";

const API_BASE = process.env.EOA_BASE_URL ?? "http://127.0.0.1:8765";

test.describe("Technology radar screen (/tech-radar) -- A12", () => {
  test("header renders and GET /api/tech/radar responds", async ({ page, request }) => {
    await page.goto("/tech-radar");
    await expect(page.locator("header h1")).toHaveText("רדאר טכנולוגי");

    const res = await request.get(`${API_BASE}/api/tech/radar?weeks=12`);
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(Array.isArray(body.subdomains)).toBe(true);
    expect(Array.isArray(body.maturities)).toBe(true);
  });

  test("renders either the radar matrix or the empty state, never a blank screen", async ({ page }) => {
    await page.goto("/tech-radar");

    const matrix = page.locator("table");
    const empty = page.getByText("אין עדיין נתוני מעקב טכנולוגי");
    await expect(async () => {
      const hasMatrix = await matrix.count();
      const hasEmpty = await empty.count();
      expect(hasMatrix > 0 || hasEmpty > 0).toBe(true);
    }).toPass({ timeout: 15_000 });
  });

  test("clicking a populated radar cell (if any) shows a filtered item list linking into /items/:id", async ({
    page,
  }) => {
    await page.goto("/tech-radar");

    const enabledCell = page.locator("table button:not([disabled])").first();
    const hasActivity = (await enabledCell.count()) > 0;
    test.skip(!hasActivity, "No tech_dev items with activity in this environment yet");

    await enabledCell.click();
    const list = page.getByRole("list");
    await expect(list).toBeVisible({ timeout: 15_000 });

    const firstLink = list.locator("a").first();
    if (await firstLink.count()) {
      const href = await firstLink.getAttribute("href");
      expect(href).toMatch(/^\/items\/\d+$/);
    }
  });

  test("GET /api/tech/items with subdomain/maturity filters responds", async ({ request }) => {
    const res = await request.get(`${API_BASE}/api/tech/items?maturity=lab&page_size=5`);
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(typeof body.total).toBe("number");
    expect(Array.isArray(body.items)).toBe(true);
  });

  test("no bad literal text on the tech radar screen", async ({ page }, testInfo) => {
    await page.goto("/tech-radar");
    await expect(page.locator("header h1")).toHaveText("רדאר טכנולוגי");
    await assertNoBadText(page, testInfo, "Technology radar (/tech-radar)");
  });
});
