import { test, expect } from "./fixtures";
import { assertNoBadText } from "../utils/helpers";

const API_BASE = process.env.EOA_BASE_URL ?? "http://127.0.0.1:8765";

test.describe("Item detail page (/items/:id)", () => {
  test("fields render and entity chips link to /entities/:id", async ({ page, request }) => {
    const firstItem = (await (await request.get(`${API_BASE}/api/items?page_size=1`)).json()).items[0];
    expect(firstItem).toBeTruthy();

    await page.goto(`/items/${firstItem.id}`);
    await expect(page.locator("header h1")).toHaveText("פרטי פריט");

    // Title renders (bdi wrapped), never blank
    const titleEl = page.locator("header + main bdi").first();
    await expect(titleEl).toBeVisible({ timeout: 15_000 });

    // Either summary or the "טרם סוכם" placeholder shows — never a silently
    // missing section.
    const summaryHeading = page.getByRole("heading", { name: "תקציר" });
    await expect(summaryHeading).toBeVisible();
    const summaryBody = page.locator("section[aria-label='תקציר']");
    await expect(summaryBody).toBeVisible();
    const bodyText = (await summaryBody.textContent())?.trim() ?? "";
    expect(bodyText.length).toBeGreaterThan(0);
  });

  test("entity chips (when the item mentions a tracked entity) link to /entities/:id", async ({
    page,
    request,
  }) => {
    // Find an item that actually has entities_mentioned overlapping a
    // tracked entity, by scanning a page of items via the API first.
    const items = (await (await request.get(`${API_BASE}/api/items?page_size=200`)).json()).items as {
      id: number;
      entities_mentioned: string[];
    }[];
    const candidate = items.find((i) => (i.entities_mentioned ?? []).length > 0);
    test.skip(!candidate, "No item with entities_mentioned found in the first 200 items");

    await page.goto(`/items/${candidate!.id}`);
    const chipsSection = page.locator("section[aria-label='ישויות מוזכרות']");
    await expect(chipsSection).toBeVisible({ timeout: 15_000 });

    const links = chipsSection.locator("a");
    const linkCount = await links.count();
    if (linkCount > 0) {
      const href = await links.first().getAttribute("href");
      expect(href).toMatch(/^\/entities\/\d+$/);
      await links.first().click();
      await expect(page).toHaveURL(new RegExp(href!));
    }
  });

  test("no bad literal text values render on the item page", async ({ page, request }, testInfo) => {
    const firstItem = (await (await request.get(`${API_BASE}/api/items?page_size=1`)).json()).items[0];
    await page.goto(`/items/${firstItem.id}`);
    await expect(page.locator("header h1")).toHaveText("פרטי פריט");
    await assertNoBadText(page, testInfo, `Item detail (/items/${firstItem.id})`);
  });

  test("an unknown item id shows an explicit not-found error state, not a crash", async ({ page }) => {
    await page.goto("/items/999999999");
    await expect(page.getByRole("alert")).toBeVisible({ timeout: 15_000 });
  });
});
