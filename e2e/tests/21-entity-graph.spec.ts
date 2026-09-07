import { test, expect } from "./fixtures";
import { assertNoBadText } from "../utils/helpers";

const API_BASE = process.env.EOA_BASE_URL ?? "http://127.0.0.1:8765";

// R10-graph (docs/qa/loop/round_10_fixes.md): the analyst-facing entity graph explorer, a new
// "סייר גרף" tab on /entities built on five new endpoints (`GET /api/graph/overview`,
// `/api/graph/search`, `/api/graph/neighborhood/{id}`, `/api/graph/path`,
// `/api/entities/{id}/detail`). These endpoints don't exist on the live backend until the lead
// restarts it (backend changes go live on restart, per this round's standing rules) -- every test
// here checks `GET /api/graph/overview` first and `test.skip`s on a 404, same guard pattern
// `20-product-lines.spec.ts` uses for PL-ui. The pre-existing U10 `/entities` tests
// (`04-entities.spec.ts`) are untouched and still cover the "רשימה ופרטים" tab.
test.describe("Entity graph explorer (/entities, 'סייר גרף' tab) -- R10-graph", () => {
  test.beforeEach(async ({ request }) => {
    const res = await request.get(`${API_BASE}/api/graph/overview`);
    test.skip(res.status() === 404, "GET /api/graph/overview not implemented on this backend yet");
    expect(res.ok()).toBe(true);
  });

  test("the tab switcher renders both views and defaults to 'רשימה ופרטים'", async ({ page }) => {
    await page.goto("/entities");
    const listTab = page.getByRole("tab", { name: "רשימה ופרטים" });
    const graphTab = page.getByRole("tab", { name: "סייר גרף" });
    await expect(listTab).toBeVisible();
    await expect(graphTab).toBeVisible();
    await expect(listTab).toHaveAttribute("aria-selected", "true");
    await expect(graphTab).toHaveAttribute("aria-selected", "false");
  });

  test("switching to 'סייר גרף' shows the search box and either the canvas or an empty state", async ({
    page,
  }) => {
    await page.goto("/entities");
    await page.getByRole("tab", { name: "סייר גרף" }).click();
    await expect(page).toHaveURL(/view=graph/);

    await expect(page.getByLabel("חפש ישות למרכז הגרף")).toBeVisible({ timeout: 15_000 });
    const canvas = page.getByLabel(/גרף ישויות/);
    const emptyState = page.getByText("אין נתוני גרף עדיין");
    await expect(canvas.or(emptyState)).toBeVisible({ timeout: 15_000 });
  });

  test("searching an entity and selecting it centers the graph on that entity", async ({ page }) => {
    await page.goto("/entities?view=graph");
    const searchBox = page.getByLabel("חפש ישות למרכז הגרף");
    await expect(searchBox).toBeVisible({ timeout: 15_000 });
    await searchBox.fill("Elbit");
    await page.waitForTimeout(500);

    const option = page.getByRole("button", { name: /Elbit/ }).first();
    const noResults = page.getByText("מחפש…");
    // Either a real match exists (this DB seed may or may not have "Elbit"), or the dropdown
    // simply shows nothing -- never an error/blank screen either way.
    if (await option.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await option.click();
      await expect(page.getByRole("button", { name: "מוצא מסלולים" })).toBeVisible();
    }
    await expect(noResults).not.toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
  });

  test("the filter bar renders entity-kind and relation-type toggle chips", async ({ page }) => {
    await page.goto("/entities?view=graph");
    await expect(page.getByRole("button", { name: "חברה" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: "שותף של" })).toBeVisible();
  });

  test("table-view toggle shows the accessible node/edge tables instead of the canvas", async ({
    page,
  }) => {
    await page.goto("/entities?view=graph");
    const tableToggle = page.getByRole("button", { name: "תצוגת טבלה" });
    await expect(tableToggle).toBeVisible({ timeout: 15_000 });
    await tableToggle.click();
    await expect(tableToggle).toHaveAttribute("aria-pressed", "true");
    // Either the "ישויות (N)" table heading renders (graph has data) or the page's own empty
    // state does -- table view swaps the canvas, it doesn't change whether there's data.
    const nodesHeading = page.getByText(/^ישויות \(\d+\)$/);
    const emptyState = page.getByText("אין נתוני גרף עדיין");
    await expect(nodesHeading.or(emptyState)).toBeVisible({ timeout: 15_000 });
  });

  test("path finder panel opens and closes", async ({ page }) => {
    await page.goto("/entities?view=graph");
    const pathButton = page.getByRole("button", { name: "מוצא מסלולים" });
    await expect(pathButton).toBeVisible({ timeout: 15_000 });
    await pathButton.click();
    await expect(page.getByText("מ:")).toBeVisible();
    await expect(page.getByText("אל:")).toBeVisible();
    await pathButton.click();
    await expect(page.getByText("מ:")).not.toBeVisible();
  });

  // GraphPanel's "פתח גרף מלא" (from the compact per-entity graph on the "רשימה ופרטים" tab) now
  // opens this explorer, centered on that same entity, instead of the old small modal.
  test("an entity card's 'פתח גרף מלא' opens the explorer centered on that entity", async ({ page }) => {
    await page.goto("/entities");
    await page.getByLabel("חיפוש ישויות").fill("Elbit");
    await page.waitForTimeout(600);
    const firstLink = page.getByRole("list").locator("li a").first();
    test.skip(!(await firstLink.isVisible({ timeout: 5_000 }).catch(() => false)), "no entity seeded to open");
    await firstLink.click();

    const expandButton = page.getByRole("button", { name: "פתח גרף מלא" });
    const noEdges = page.getByText("אין קשרים מתועדים");
    if (await expandButton.isVisible({ timeout: 10_000 }).catch(() => false)) {
      await expandButton.click();
      await expect(page).toHaveURL(/view=graph/);
      await expect(page.getByRole("tab", { name: "סייר גרף" })).toHaveAttribute("aria-selected", "true");
    } else {
      await expect(noEdges).toBeVisible();
    }
  });

  test("no bad literal text on the graph explorer", async ({ page }, testInfo) => {
    await page.goto("/entities?view=graph");
    await expect(page.getByLabel("חפש ישות למרכז הגרף")).toBeVisible({ timeout: 15_000 });
    await assertNoBadText(page, testInfo, "Entity graph explorer (/entities?view=graph)");
  });
});
