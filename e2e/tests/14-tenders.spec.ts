import { test, expect } from "./fixtures";
import { assertNoBadText } from "../utils/helpers";

/**
 * /tenders — two tabs: "מכרזים פתוחים" (open tenders table) and "תחזית
 * מכרזים" (forecast cards). Added in the latest rebuild
 * (web/src/pages/TendersPage.tsx, web/src/components/tenders/*).
 */

test.describe("Tenders screen (/tenders)", () => {
  test("nav routes here and the page heading is correct", async ({ page }) => {
    await page.goto("/");
    await page
      .getByRole("navigation", { name: "ניווט ראשי" })
      .getByRole("link", { name: "מכרזים והזדמנויות" })
      .click();
    await expect(page).toHaveURL(/\/tenders/);
    await expect(page.locator("header h1")).toHaveText("מכרזים והזדמנויות");
  });

  test("open-tenders tab: rows render or the Hebrew empty state shows", async ({ page }) => {
    await page.goto("/tenders");
    const tablist = page.getByRole("tablist", { name: "מכרזים והזדמנויות" });
    await expect(tablist).toBeVisible({ timeout: 15_000 });
    await expect(tablist.getByRole("tab", { name: "מכרזים פתוחים" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    const empty = page.getByText("אין מכרזים פתוחים כרגע");
    const rows = page.locator("tbody tr[aria-expanded]");
    await expect(async () => {
      const [emptyVisible, rowCount] = await Promise.all([
        empty.isVisible().catch(() => false),
        rows.count(),
      ]);
      expect(emptyVisible || rowCount > 0, "expected either the empty state or ≥1 tender row").toBeTruthy();
    }).toPass({ timeout: 15_000 });
  });

  test("open-tenders: title links open in a new tab with an http(s) href", async ({ page }) => {
    await page.goto("/tenders");
    await expect(page.getByRole("status").filter({ hasText: "טוען" })).toHaveCount(0, { timeout: 15_000 });
    const rows = page.locator("tbody tr[aria-expanded]");
    const hasRows = (await rows.count()) > 0;
    test.skip(!hasRows, "No tenders to check title links on");

    const n = await rows.count();
    let checked = 0;
    for (let i = 0; i < n; i++) {
      const link = rows.nth(i).locator("td").nth(2).locator("a");
      if ((await link.count()) === 0) continue;
      checked++;
      const href = await link.getAttribute("href");
      expect(href, `row ${i} title link href`).toMatch(/^https?:\/\//);
      await expect(link, `row ${i} title link target`).toHaveAttribute("target", "_blank");
    }
    // Every tender in this environment carries a source URL (verified via
    // GET /api/tenders), so at least one row should have a real link.
    expect(checked).toBeGreaterThan(0);
  });

  test("open-tenders: deadline chips render for every row (a formatted date, or an em dash when there is none)", async ({
    page,
  }) => {
    await page.goto("/tenders");
    await expect(page.getByRole("status").filter({ hasText: "טוען" })).toHaveCount(0, { timeout: 15_000 });
    const rows = page.locator("tbody tr[aria-expanded]");
    const hasRows = (await rows.count()) > 0;
    test.skip(!hasRows, "No tenders to check deadline chips on");

    const n = await rows.count();
    for (let i = 0; i < n; i++) {
      const deadlineCell = rows.nth(i).locator("td").nth(1);
      const text = (await deadlineCell.innerText()).trim();
      expect(text.length, `row ${i} deadline cell should not be blank`).toBeGreaterThan(0);
      expect(text).not.toMatch(/^(NaN|undefined|null|\[object Object\])$/);
    }
  });

  test("expanding an open-tender row reveals its detail (summary/entities/linked item)", async ({ page }) => {
    await page.goto("/tenders");
    await expect(page.getByRole("status").filter({ hasText: "טוען" })).toHaveCount(0, { timeout: 15_000 });
    const rows = page.locator("tbody tr[aria-expanded]");
    const hasRows = (await rows.count()) > 0;
    test.skip(!hasRows, "No tenders to expand");

    // Click the leading chevron cell, not the row's default (center)
    // point — every tender in this environment has a source URL, so the
    // title cell holds an <a target="_blank" onClick={stopPropagation}>
    // (TenderTable.tsx) that would swallow a row-centered click and never
    // reach the <tr>'s onClick toggle.
    const firstRow = rows.first();
    await expect(firstRow).toHaveAttribute("aria-expanded", "false");
    await firstRow.locator("td").first().click();
    await expect(firstRow).toHaveAttribute("aria-expanded", "true");
    // the detail row is the sibling <tr> immediately after
    const detailRow = firstRow.locator("xpath=following-sibling::tr[1]");
    await expect(detailRow).toBeVisible();
    await firstRow.locator("td").first().click();
    await expect(firstRow).toHaveAttribute("aria-expanded", "false");
  });

  test("forecast tab: cards render or the Hebrew empty state shows, and rationale [item N] tokens link to /items/N", async ({
    page,
  }) => {
    await page.goto("/tenders");
    const tablist = page.getByRole("tablist", { name: "מכרזים והזדמנויות" });
    await expect(tablist).toBeVisible({ timeout: 15_000 });
    await tablist.getByRole("tab", { name: "תחזית מכרזים" }).click();
    await expect(page).toHaveURL(/tab=forecast/);
    await expect(tablist.getByRole("tab", { name: "תחזית מכרזים" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    const empty = page.getByText("אין תחזיות מכרזים כרגע");
    const cards = page.locator("main .grid > div.rounded-lg.border.border-border.bg-bg-raised");
    await expect(async () => {
      const [emptyVisible, cardCount] = await Promise.all([
        empty.isVisible().catch(() => false),
        cards.count(),
      ]);
      expect(emptyVisible || cardCount > 0, "expected either the empty state or ≥1 forecast card").toBeTruthy();
    }).toPass({ timeout: 15_000 });

    const cardCount = await cards.count();
    if (cardCount > 0) {
      const itemLinks = page.locator('main a[href^="/items/"]');
      const itemLinkCount = await itemLinks.count();
      if (itemLinkCount > 0) {
        const href = await itemLinks.first().getAttribute("href");
        expect(href).toMatch(/^\/items\/\d+$/);
        const linkText = (await itemLinks.first().textContent())?.trim() ?? "";
        expect(linkText).toMatch(/^\[item \d+\]$/);
      }
    }
  });

  test("switching tabs updates the ?tab= query param and survives a reload", async ({ page }) => {
    await page.goto("/tenders");
    const tablist = page.getByRole("tablist", { name: "מכרזים והזדמנויות" });
    await expect(tablist).toBeVisible({ timeout: 15_000 });
    await tablist.getByRole("tab", { name: "תחזית מכרזים" }).click();
    await expect(page).toHaveURL(/tab=forecast/);
    await page.reload();
    await expect(tablist.getByRole("tab", { name: "תחזית מכרזים" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  // F24 (docs/QA_PROGRAM.md section 4, 2026-09-06): header status-count chips + the "show
  // closed/archived" toggle. Tolerant of the pre-restart API version skew documented in
  // docs/MODULES.md's F23/F24 section (the running API process must restart to pick up the new
  // `{tenders, counts}` /api/tenders response shape -- until then the page correctly shows its
  // empty state and neither the chips nor the toggle render, which this test accepts rather than
  // failing on).
  test("status count chips and the show-closed/archived toggle behave once tenders exist", async ({ page }) => {
    await page.goto("/tenders");
    await expect(page.getByRole("status").filter({ hasText: "טוען" })).toHaveCount(0, { timeout: 15_000 });

    const chips = page.getByRole("list", { name: "ספירת מכרזים לפי סטטוס" });
    const toggle = page.getByRole("checkbox");
    // The chips <div role="list"> is always in the DOM once loaded, even with zero counts (it
    // just renders no <span> children then) -- check for an actual chip, not just the container.
    const hasChips = (await chips.locator("[role='listitem']").count()) > 0;
    test.skip(!hasChips, "No tenders in any status yet (or the API hasn't restarted onto the new /api/tenders contract)");

    await expect(toggle).toBeVisible();
    const rowsBefore = await page.locator("tbody tr[aria-expanded]").count();
    await toggle.check();
    // Toggling must not error out -- either more rows appear (closed/archived included) or the
    // count stays the same (none exist), but the table/empty-state must still render cleanly.
    await expect(async () => {
      const rowsAfter = await page.locator("tbody tr[aria-expanded]").count();
      expect(rowsAfter).toBeGreaterThanOrEqual(rowsBefore);
    }).toPass({ timeout: 5_000 });
  });

  // Q5-11 (docs/qa/findings_Q5_r2.md): the default (open/unknown) view can be empty while closed/
  // archived tenders exist, hidden only because `since_days` used to stay at 90 days even after
  // `include_closed`/`include_archived` widened the status set. The fix lifts that window (unless
  // an explicit since_days is given) and the UI now shows an inline "X מכרזים סגורים מוסתרים —
  // הצג" hint instead of a dead-end "no matches" message.
  test("when the default view is empty but closed/archived rows exist, an inline hint reveals them", async ({
    page,
  }) => {
    await page.goto("/tenders");
    await expect(page.getByRole("status").filter({ hasText: "טוען" })).toHaveCount(0, { timeout: 15_000 });

    const chips = page.getByRole("list", { name: "ספירת מכרזים לפי סטטוס" });
    const hasChips = (await chips.locator("[role='listitem']").count()) > 0;
    test.skip(!hasChips, "No tenders in any status yet (or the API hasn't restarted onto the new /api/tenders contract)");

    const rowsBefore = await page.locator("tbody tr[aria-expanded]").count();
    const hint = page.getByText(/מכרזים סגורים מוסתרים בתצוגה הנוכחית/);
    const hintVisible = await hint.isVisible().catch(() => false);
    test.skip(
      rowsBefore > 0 || !hintVisible,
      "Default view already has open/unknown rows, or no closed/archived rows are hidden right now",
    );

    const cta = page.getByRole("button", { name: /הצג \d+ מכרזים סגורים/ });
    await expect(cta).toBeVisible();
    await cta.click();
    // Clicking the inline action must check the same toggle the header checkbox controls, and
    // actually reveal rows (not leave the "no matches" dead end in place).
    await expect(page.getByRole("checkbox")).toBeChecked();
    await expect(async () => {
      const rowsAfter = await page.locator("tbody tr[aria-expanded]").count();
      expect(rowsAfter).toBeGreaterThan(0);
    }).toPass({ timeout: 5_000 });
  });

  test("no bad literal text renders on either tab", async ({ page }, testInfo) => {
    await page.goto("/tenders");
    await expect(page.getByRole("tablist", { name: "מכרזים והזדמנויות" })).toBeVisible({
      timeout: 15_000,
    });
    await assertNoBadText(page, testInfo, "Tenders — open (/tenders)");

    await page.getByRole("tab", { name: "תחזית מכרזים" }).click();
    await page.waitForTimeout(500);
    await assertNoBadText(page, testInfo, "Tenders — forecast (/tenders?tab=forecast)");
  });
});
