import { test, expect } from "./fixtures";
import { assertNoBadText, recordFinding } from "../utils/helpers";

const API_BASE = process.env.EOA_BASE_URL ?? "http://127.0.0.1:8765";

test.describe("Feed screen (/feed)", () => {
  test("rows render and any displayed item-count reflects the API total", async ({ page, request }, testInfo) => {
    const apiTotal = (await (await request.get(`${API_BASE}/api/items?page_size=1`)).json()).total;
    expect(typeof apiTotal).toBe("number");

    await page.goto("/feed");
    const firstRow = page.locator('[data-testid^="feed-row-"]').first();
    await expect(firstRow).toBeVisible({ timeout: 20_000 });

    // docs/MODULES.md documents "מוצגים X מתוך Y פריטים" (U5, 2026-09-05 —
    // a plain Hebrew status sentence, replacing the old mixed-language
    // "מציג X מתוך Y · ניווט: J/K · ..." cheat-sheet line). The
    // currently-deployed build may predate either and only show a bare
    // total ("N פריטים") — accept any of the three, but flag the oldest
    // copy as a finding rather than silently treating it as equivalent.
    const newFormat = page.locator("text=/מ(?:ציג|וצגים) \\d+ מתוך \\d+/");
    const oldFormat = page.locator("text=/^\\d+ פריטים/");

    if (await newFormat.count()) {
      const text = (await newFormat.first().textContent())!;
      const total = Number(text.match(/מתוך (\d+)/)![1]);
      expect(total).toBe(apiTotal);
      return;
    }

    if (await oldFormat.count()) {
      const text = (await oldFormat.first().textContent())!;
      await recordFinding(page, testInfo, {
        screen: "Feed (/feed)",
        expected:
          'Per docs/MODULES.md, the feed status line reads "מציג X מתוך Y" (rows currently shown vs. API total)',
        actual: `Deployed build shows only a bare total, no "shown" breakdown: "${text.trim()}"`,
        severity: "low",
      });
      const total = Number(text.match(/(\d+) פריטים/)![1]);
      expect(total, "the bare total count should still match the API total").toBe(apiTotal);
      return;
    }

    await recordFinding(page, testInfo, {
      screen: "Feed (/feed)",
      expected: "Feed shows an item-count indicator consistent with the API total",
      actual: "No item-count text found on the feed screen (checked both the current and legacy copy)",
      severity: "medium",
    });
    expect(false, "No item-count text found on the feed screen in either known format").toBeTruthy();
  });

  test("scrolling / \"טען עוד\" loads more rows until all are shown or ≥ 200 rows are loaded", async ({
    page,
  }, testInfo) => {
    const apiTotal = (await (await page.request.get(`${API_BASE}/api/items?page_size=1`)).json()).total;
    const target = Math.min(200, apiTotal);

    // Rows are windowed/virtualized (only viewport rows exist in the DOM at
    // once), so the DOM row count is not a reliable "how many are loaded"
    // signal. Track it at the network layer instead: the set of distinct
    // item ids seen across every /api/items response is exactly "how many
    // rows the feed has fetched into memory so far", regardless of how the
    // UI renders/virtualizes them.
    const loadedItemIds = new Set<number>();
    page.on("response", (res) => {
      if (!res.url().includes("/api/items?") || res.request().method() !== "GET" || !res.ok()) return;
      res
        .json()
        .then((body) => {
          for (const it of body.items ?? []) loadedItemIds.add(it.id);
        })
        .catch(() => {});
    });

    await page.goto("/feed");
    await expect(page.locator('[data-testid^="feed-row-"]').first()).toBeVisible({ timeout: 20_000 });
    await page.waitForTimeout(1000); // let the first response's body be parsed by the listener above

    const list = page.locator('[data-testid="feed-list"]');
    const loadMoreBtn = page.getByRole("button", { name: /טען עוד/ });

    for (let i = 0; i < 8 && loadedItemIds.size < target; i++) {
      if (await loadMoreBtn.count()) {
        await loadMoreBtn.click();
      } else {
        await list.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      }
      await page.waitForTimeout(900);
    }

    if (loadedItemIds.size < target) {
      await recordFinding(page, testInfo, {
        screen: "Feed (/feed)",
        expected: `Scrolling to the bottom (or a "טען עוד" control) fetches further pages until ≥ ${target} of ${apiTotal} items are loaded`,
        actual: `Only ${loadedItemIds.size} distinct item(s) were ever fetched via /api/items; no further page requests fired on scroll and no "טען עוד" control exists`,
        severity: "high",
      });
    }
    expect(
      loadedItemIds.size,
      `Only ${loadedItemIds.size} distinct items were loaded across all /api/items responses (target ${target} of ${apiTotal})`,
    ).toBeGreaterThanOrEqual(target);
  });

  test("every row's title link (where present) has an http(s) href and target=_blank", async ({
    page,
  }, testInfo) => {
    await page.goto("/feed");
    await expect(page.locator('[data-testid^="feed-row-"]').first()).toBeVisible({ timeout: 20_000 });

    const rows = page.locator('[data-testid^="feed-row-"]');
    const n = await rows.count();
    let linkedRowCount = 0;
    for (let i = 0; i < n; i++) {
      const link = rows.nth(i).locator('a[data-testid^="feed-row-title-link-"]');
      if ((await link.count()) === 0) continue;
      linkedRowCount++;
      const href = await link.getAttribute("href");
      if (href === null || href === "") continue; // items with no source url render a non-link span/anchor without href
      expect(href, `row ${i} title link href`).toMatch(/^https?:\/\//);
      await expect(link, `row ${i} title link target`).toHaveAttribute("target", "_blank");
    }

    if (linkedRowCount === 0) {
      await recordFinding(page, testInfo, {
        screen: "Feed (/feed)",
        expected: "Each feed row's title is an <a href=\"...\" target=\"_blank\"> to its source",
        actual: `None of the ${n} visible rows render a title anchor at all (checked data-testid="feed-row-title-link-*")`,
        severity: "high",
      });
    }
    expect(linkedRowCount, "at least one visible row should render a title link").toBeGreaterThan(0);
  });

  test("level filter narrows the list to only the selected level(s)", async ({ page }) => {
    await page.goto("/feed");
    await expect(page.locator('[data-testid^="feed-row-"]').first()).toBeVisible({ timeout: 20_000 });

    const redToggle = page.getByRole("group", { name: "סינון לפי רמה" }).getByRole("button", { name: "קריטי" });
    // Register the response wait BEFORE the click that triggers it — the
    // refetch can resolve faster than a wait registered after the click,
    // which would otherwise miss the event and time out.
    const [response] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/items") && r.url().includes("level=red")),
      redToggle.click(),
    ]);
    expect(response.ok()).toBeTruthy();
    await expect(redToggle).toHaveAttribute("aria-pressed", "true");

    // Re-read the row list fresh right before asserting each level — the
    // feed is virtualized, so a count captured too early (mid-refetch) can
    // go stale by the time we reach a later index.
    await page.waitForTimeout(300);

    const levelBadges = page.locator('[data-testid^="feed-row-"] [data-level]');
    const n = await levelBadges.count();
    for (let i = 0; i < n; i++) {
      await expect(levelBadges.nth(i)).toHaveAttribute("data-level", "red");
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

  test("keyboard: J/K move selection, Enter navigates to the item's full page (/items/:id)", async ({
    page,
  }) => {
    // NOTE: as of the latest rebuild, Enter no longer opens the inline
    // FeedDetailPanel — FeedPage.tsx's onKeyDown now calls
    // navigate(`/items/${selected.id}`) directly (full route navigation to
    // the new ItemDetailPage). The inline panel is opened by double-clicking
    // a row instead (FeedRow's onDoubleClick={onOpen}); see the next test.
    await page.goto("/feed");
    const firstRow = page.locator('[data-testid^="feed-row-"]').first();
    await expect(firstRow).toBeVisible({ timeout: 20_000 });
    await expect(firstRow).toHaveAttribute("data-selected", "true");
    const firstRowId = (await firstRow.getAttribute("data-testid"))!.replace("feed-row-", "");

    await page.keyboard.press("j");
    await page.waitForTimeout(200);
    await expect(firstRow).toHaveAttribute("data-selected", "false");
    const secondSelected = page.locator('[data-testid^="feed-row-"][data-selected="true"]');
    await expect(secondSelected).toHaveCount(1);
    const secondRowId = (await secondSelected.getAttribute("data-testid"))!.replace("feed-row-", "");

    await page.keyboard.press("k");
    await page.waitForTimeout(200);
    await expect(firstRow).toHaveAttribute("data-selected", "true");

    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(new RegExp(`/items/${firstRowId}$`), { timeout: 10_000 });
    await expect(page.locator("header h1")).toHaveText("פרטי פריט");
    void secondRowId;
  });

  test("double-clicking a row opens the inline detail panel (without navigating away)", async ({ page }) => {
    await page.goto("/feed");
    const firstRow = page.locator('[data-testid^="feed-row-"]').first();
    await expect(firstRow).toBeVisible({ timeout: 20_000 });

    await firstRow.dblclick();
    await expect(page.locator('[data-testid="feed-detail-panel"]')).toBeVisible({ timeout: 10_000 });
    await expect(page).toHaveURL(/\/feed/); // stays on the feed, unlike Enter
  });

  test("keyboard: Space opens the inline quick-preview panel for the selected row (without navigating away)", async ({
    page,
  }) => {
    // Mirrors the double-click gesture above: Space is the "quick look" key
    // (stays on /feed, opens FeedDetailPanel via setOpenItemId), Enter is
    // the "open full page" key (navigates to /items/:id — see the dedicated
    // Enter test above). Confirmed against FeedPage.tsx's onKeyDown, which
    // has an explicit `e.key === " " || e.key === "Spacebar"` branch.
    await page.goto("/feed");
    const firstRow = page.locator('[data-testid^="feed-row-"]').first();
    await expect(firstRow).toBeVisible({ timeout: 20_000 });
    await expect(firstRow).toHaveAttribute("data-selected", "true");

    await page.keyboard.press(" ");
    await expect(page.locator('[data-testid="feed-detail-panel"]')).toBeVisible({ timeout: 10_000 });
    await expect(page).toHaveURL(/\/feed/);
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
      const link = rows.nth(i).locator('a[data-testid^="feed-row-title-link-"]');
      if ((await link.count()) === 0) continue;
      const href = await link.getAttribute("href");
      if (href) {
        targetIndex = i;
        break;
      }
    }
    test.skip(targetIndex === -1, "No row with a source URL / title link found on the first page of the feed");

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
    // Double-click opens the inline panel (Enter now navigates to /items/:id
    // instead — see the dedicated Enter-navigation test above).
    await firstRow.dblclick();

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
