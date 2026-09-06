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

  // F23 (docs/QA_PROGRAM.md section 4, 2026-09-06): `[n]` citations must be real, single (not
  // nested) links.
  test("citation markers are single anchors (no nested <a>)", async ({ page, request }) => {
    const reports = await (await request.get(`${API_BASE}/api/reports?limit=30`)).json();
    test.skip(!Array.isArray(reports) || reports.length === 0, "No reports exist in this environment");

    await page.goto(`/reports?id=${reports[0].id}`);
    const article = page.locator("article");
    await expect(article).toBeVisible({ timeout: 15_000 });

    const citations = article.locator("a.cite");
    const count = await citations.count();
    test.skip(count === 0, "This report has no [n] citation markers");

    // No citation anchor should itself contain another <a> (the reportHtml.ts nesting bug F23 fixed).
    for (let i = 0; i < count; i++) {
      const nestedAnchors = await citations.nth(i).locator("a").count();
      expect(nestedAnchors, `citation #${i} must not nest another <a>`).toBe(0);
    }
  });

  // W4 (docs/REVIEW_2026-09-06_evening.md round 4): a resolved `[n]` marker used to navigate
  // straight to /items/:id (F23/U3) -- which never actually opened the cited source. It must now
  // (a) scroll to and highlight its row in the sources appendix, and (b) both the appendix row and
  // the [n] marker's own hover tooltip must offer a real "פתח מקור" link that opens the source URL
  // in a new tab.
  test("a resolved [n] scrolls to its appendix row (not /items/:id) and exposes a real open-source link", async ({
    page,
    request,
  }) => {
    const reports = await (await request.get(`${API_BASE}/api/reports?limit=30`)).json();
    test.skip(!Array.isArray(reports) || reports.length === 0, "No reports exist in this environment");

    await page.goto(`/reports?id=${reports[0].id}`);
    const article = page.locator("article");
    await expect(article).toBeVisible({ timeout: 15_000 });

    const marker = article.locator("a.eo-citation[data-n]").first();
    test.skip((await marker.count()) === 0, "This report has no resolved [n] citation markers");

    const n = await marker.getAttribute("data-n");
    const beforeUrl = page.url();
    await marker.click();
    // Stays on the report page -- no navigation to /items/:id.
    await expect(page).toHaveURL(beforeUrl);

    const appendixRow = page.locator(`#src-${n}`);
    await expect(appendixRow).toBeInViewport();

    // The appendix row's own "פתח מקור" link (if this citation has a source URL) opens in a new
    // tab with noopener.
    const openLink = appendixRow.getByRole("link", { name: /פתח מקור/ });
    if ((await openLink.count()) > 0) {
      await expect(openLink).toHaveAttribute("target", "_blank");
      await expect(openLink).toHaveAttribute("rel", /noopener/);
      const href = await openLink.getAttribute("href");
      expect(href).toMatch(/^https?:\/\//);
    }

    // Hovering the marker itself surfaces the same open-source action in a tooltip.
    await marker.hover();
    const tooltip = page.getByRole("tooltip");
    await expect(tooltip).toBeVisible();
    const tooltipOpenLink = tooltip.getByRole("link", { name: /פתח מקור/ });
    if ((await tooltipOpenLink.count()) > 0) {
      await expect(tooltipOpenLink).toHaveAttribute("target", "_blank");
      await expect(tooltipOpenLink).toHaveAttribute("rel", /noopener/);
    }
  });
});
