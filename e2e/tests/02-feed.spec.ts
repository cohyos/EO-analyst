import { test, expect } from "./fixtures";
import { assertNoBadText } from "../utils/helpers";

const API_BASE = process.env.EOA_BASE_URL ?? "http://127.0.0.1:8765";

test.describe("Feed screen (/feed)", () => {
  test("rows render and the count text is consistent with the API total", async ({ page, request }) => {
    const apiTotal = (await (await request.get(`${API_BASE}/api/items?page_size=1`)).json()).total;
    expect(typeof apiTotal).toBe("number");

    await page.goto("/feed");
    const firstRow = page.locator('[data-testid^="feed-row-"]').first();
    await expect(firstRow).toBeVisible({ timeout: 20_000 });

    const countText = page.locator("text=/מציג \\d+ מתוך \\d+/");
    await expect(countText).toBeVisible();
    const text = await countText.textContent();
    const match = text!.match(/מציג (\d+) מתוך (\d+)/);
    expect(match).not.toBeNull();
    const [, , totalShown] = match!;
    expect(Number(totalShown)).toBe(apiTotal);
  });

  test("scrolling / \"טען עוד\" loads more rows until all are shown or ≥ 200 rows are loaded", async ({
    page,
  }) => {
    await page.goto("/feed");
    const list = page.locator('[data-testid="feed-list"]');
    await expect(list).toBeVisible({ timeout: 20_000 });

    const countText = page.locator("text=/מציג \\d+ מתוך \\d+/");
    await expect(countText).toBeVisible();
    const initialTotal = Number((await countText.textContent())!.match(/מתוך (\d+)/)![1]);

    let loadedCount = (await page.locator('[data-testid^="feed-row-"]').count());

    const loadMoreBtn = page.getByRole("button", { name: /טען עוד/ });
    for (let i = 0; i < 5; i++) {
      loadedCount = Number((await countText.textContent())!.match(/מציג (\d+) /)![1]);
      if (loadedCount >= Math.min(200, initialTotal)) break;
      if (await loadMoreBtn.count()) {
        await loadMoreBtn.click();
      } else {
        // fall back to scroll-triggered pagination
        await list.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      }
      await page.waitForTimeout(800);
    }

    loadedCount = Number((await countText.textContent())!.match(/מציג (\d+) /)![1]);
    expect(loadedCount).toBeGreaterThanOrEqual(Math.min(200, initialTotal));
  });

  test("every row's title link has an http(s) href and target=_blank", async ({ page }) => {
    await page.goto("/feed");
    await expect(page.locator('[data-testid^="feed-row-"]').first()).toBeVisible({ timeout: 20_000 });

    const titleLinks = page.locator('[data-testid^="feed-row-title-link-"]');
    const n = await titleLinks.count();
    expect(n).toBeGreaterThan(0);
    for (let i = 0; i < n; i++) {
      const link = titleLinks.nth(i);
      const href = await link.getAttribute("href");
      if (href === null || href === "") continue; // items with no source url render a non-link span/anchor without href
      expect(href, `row ${i} title link href`).toMatch(/^https?:\/\//);
      await expect(link, `row ${i} title link target`).toHaveAttribute("target", "_blank");
    }
  });

  test("level filter narrows the list to only the selected level(s)", async ({ page }) => {
    await page.goto("/feed");
    await expect(page.locator('[data-testid^="feed-row-"]').first()).toBeVisible({ timeout: 20_000 });

    const redToggle = page.getByRole("group", { name: "סינון לפי רמה" }).getByRole("button", { name: "קריטי" });
    await redToggle.click();
    await expect(redToggle).toHaveAttribute("aria-pressed", "true");

    // wait for the list to refetch with the filter applied
    await page.waitForTimeout(500);
    const rows = page.locator('[data-testid^="feed-row-"]');
    const n = await rows.count();
    if (n > 0) {
      for (let i = 0; i < n; i++) {
        await expect(rows.nth(i).locator('[data-level]')).toHaveAttribute("data-level", "red");
      }
    }
  });

  test("free-text search narrows the list", async ({ page }) => {
    await page.goto("/feed");
    await expect(page.locator('[data-testid^="feed-row-"]').first()).toBeVisible({ timeout: 20_000 });
    const before = await page.locator('[data-testid^="feed-row-"]').count();

    const search = page.getByLabel("חיפוש בפיד");
    await search.fill("zzzz_no_such_item_should_exist_zzzz");
    await page.waitForTimeout(600);

    const empty = page.getByText("אין פריטים תואמים");
    await expect(empty).toBeVisible({ timeout: 10_000 });
    void before;
  });

  test("domain filter changes the query", async ({ page }) => {
    await page.goto("/feed");
    await expect(page.locator('[data-testid^="feed-row-"]').first()).toBeVisible({ timeout: 20_000 });

    let sawFilteredRequest = false;
    page.on("request", (req) => {
      if (req.url().includes("/api/items") && req.url().includes("domain=")) {
        sawFilteredRequest = true;
      }
    });
    const domainSelect = page.getByLabel("סינון לפי תחום");
    const options = await domainSelect.locator("option").allTextContents();
    expect(options.length).toBeGreaterThan(1);
    await domainSelect.selectOption({ index: 1 });
    await page.waitForTimeout(500);
    expect(sawFilteredRequest).toBeTruthy();
  });

  test("keyboard: J/K move selection, Enter opens the detail panel", async ({ page }) => {
    await page.goto("/feed");
    const firstRow = page.locator('[data-testid^="feed-row-"]').first();
    await expect(firstRow).toBeVisible({ timeout: 20_000 });
    await expect(firstRow).toHaveAttribute("data-selected", "true");

    await page.keyboard.press("j");
    await page.waitForTimeout(200);
    await expect(firstRow).toHaveAttribute("data-selected", "false");
    const secondSelected = page.locator('[data-testid^="feed-row-"][data-selected="true"]');
    await expect(secondSelected).toHaveCount(1);

    await page.keyboard.press("k");
    await page.waitForTimeout(200);
    await expect(firstRow).toHaveAttribute("data-selected", "true");

    await page.keyboard.press("Enter");
    await expect(page.locator('[data-testid="feed-detail-panel"]')).toBeVisible({ timeout: 10_000 });
  });

  test("keyboard: 1-4 re-rates the selected row via POST /api/items/{id}/feedback", async ({ page }) => {
    await page.goto("/feed");
    const firstRow = page.locator('[data-testid^="feed-row-"]').first();
    await expect(firstRow).toBeVisible({ timeout: 20_000 });
    const rowTestId = await firstRow.getAttribute("data-testid");
    const itemId = rowTestId!.replace("feed-row-", "");

    // Capture the item's current level so we can restore it after the test
    // — this hits the real backend and would otherwise permanently mutate
    // shared demo data every run.
    const before = await (await page.request.get(`${API_BASE}/api/items/${itemId}`)).json();
    const originalLevel: string = before.level;

    const [response] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes(`/api/items/${itemId}/feedback`) && r.request().method() === "POST",
      ),
      page.keyboard.press("3"), // "yellow" — least likely to disrupt triage badly if restore fails
    ]);
    expect(response.ok()).toBeTruthy();

    // restore
    await page.request.post(`${API_BASE}/api/items/${itemId}/feedback`, {
      data: { user_level: originalLevel, comment: null },
    });
  });

  test('"O" opens the source in a new tab/popup', async ({ page, context }) => {
    await page.goto("/feed");
    const firstRow = page.locator('[data-testid^="feed-row-"]').first();
    await expect(firstRow).toBeVisible({ timeout: 20_000 });

    // find a row that actually has a source url, select it, then press O
    const rows = page.locator('[data-testid^="feed-row-"]');
    const n = await rows.count();
    let targetIndex = -1;
    for (let i = 0; i < n; i++) {
      const href = await rows.nth(i).locator('a[data-testid^="feed-row-title-link-"]').getAttribute("href");
      if (href) {
        targetIndex = i;
        break;
      }
    }
    test.skip(targetIndex === -1, "No row with a source URL found on the first page of the feed");

    for (let i = 0; i < targetIndex; i++) await page.keyboard.press("j");

    const [popup] = await Promise.all([
      context.waitForEvent("page", { timeout: 10_000 }),
      page.keyboard.press("o"),
    ]);
    await popup.waitForLoadState("domcontentloaded", { timeout: 15_000 }).catch(() => {});
    expect(popup.url()).toMatch(/^https?:\/\//);
    await popup.close();
  });

  test("detail panel never renders blank: shows a summary or an explicit \"not yet summarized\" state", async ({
    page,
  }) => {
    await page.goto("/feed");
    const firstRow = page.locator('[data-testid^="feed-row-"]').first();
    await expect(firstRow).toBeVisible({ timeout: 20_000 });
    await firstRow.click();
    await page.keyboard.press("Enter");

    const panel = page.locator('[data-testid="feed-detail-panel"]');
    await expect(panel).toBeVisible({ timeout: 10_000 });

    // Panel must never be visually/semantically empty: it always has the
    // header (title) plus at least one of: a summary section, a triage
    // "טרם סוכם"/"אין נימוק" placeholder, or key-facts/entities content.
    const hasSummarySection = await panel.getByRole("heading", { name: "תקציר" }).count();
    const hasNotYetSummarized = await panel.getByText(/טרם סוכם/).count();
    const hasAnyBodyContent = await panel
      .locator("div.min-h-0.flex-1 > *")
      .count();

    expect(
      hasSummarySection > 0 || hasNotYetSummarized > 0,
      'FeedDetailPanel should show either a "תקציר" section or an explicit "טרם סוכם" placeholder when summary_he is empty, never silently omit the section',
    ).toBeTruthy();
    expect(hasAnyBodyContent).toBeGreaterThan(0);
  });

  test("no bad literal text values render anywhere on the feed screen", async ({ page }, testInfo) => {
    await page.goto("/feed");
    await expect(page.locator('[data-testid^="feed-row-"]').first()).toBeVisible({ timeout: 20_000 });
    await assertNoBadText(page, testInfo, "Feed (/feed)");
  });
});
