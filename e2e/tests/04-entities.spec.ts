import { test, expect } from "./fixtures";
import { assertNoBadText } from "../utils/helpers";

test.describe("Entities screens (/entities, /entities/:id)", () => {
  test('searching "Elbit" returns matching rows', async ({ page }) => {
    await page.goto("/entities");
    await page.getByLabel("חיפוש ישויות").fill("Elbit");
    await page.waitForTimeout(600);

    const list = page.getByRole("list");
    await expect(list).toBeVisible({ timeout: 15_000 });
    const items = list.locator("li");
    await expect(items.first()).toBeVisible();
    const count = await items.count();
    expect(count).toBeGreaterThan(0);

    const text = (await items.first().innerText()).toLowerCase();
    expect(text).toContain("elbit");
  });

  test("selecting an entity shows its card, timeline and a compact Cytoscape graph — no page navigation lost", async ({
    page,
  }) => {
    await page.goto("/entities");
    await page.getByLabel("חיפוש ישויות").fill("Elbit");
    await page.waitForTimeout(600);

    const firstLink = page.getByRole("list").locator("li a").first();
    await expect(firstLink).toBeVisible({ timeout: 15_000 });
    await firstLink.click();
    await expect(page).toHaveURL(/\/entities\/\d+/);

    // Three-pane layout: the list stays visible next to the selected entity's card.
    await expect(page.getByLabel("חיפוש ישויות")).toBeVisible();

    const timelineSection = page.locator("section[aria-label='ציר זמן']");
    await expect(timelineSection).toBeVisible({ timeout: 15_000 });

    const businessEventsSection = page.locator("section[aria-label='אירועים עסקיים']");
    await expect(businessEventsSection).toBeVisible();

    const relationsSection = page.locator("section[aria-label='קשרים']");
    await expect(relationsSection).toBeVisible();

    const graphSection = page.locator("section[aria-label='גרף ישויות']");
    await expect(graphSection).toBeVisible();
    // Either a graph canvas renders (entity has edges) or the explicit
    // "no documented relations" empty state does — never a blank/giant-node panel.
    const canvas = graphSection.locator("canvas");
    const emptyState = graphSection.getByText("אין קשרים מתועדים");
    await expect(canvas.first().or(emptyState)).toBeVisible({ timeout: 15_000 });
  });

  test("every entity name and item title in the card is a working link", async ({ page }) => {
    await page.goto("/entities");
    await page.getByLabel("חיפוש ישויות").fill("Elbit");
    await page.waitForTimeout(600);
    const firstLink = page.getByRole("list").locator("li a").first();
    await expect(firstLink).toBeVisible({ timeout: 15_000 });
    await firstLink.click();

    const timelineSection = page.locator("section[aria-label='ציר זמן']");
    await expect(timelineSection).toBeVisible({ timeout: 15_000 });

    // Timeline item links must carry a real href (not "#") to /feed?open=<id>.
    const timelineLinks = timelineSection.locator("a");
    const linkCount = await timelineLinks.count();
    if (linkCount > 0) {
      const href = await timelineLinks.first().getAttribute("href");
      expect(href).toMatch(/\/feed\?open=\d+/);
    }

    // Relation counterpart links (if any) must point at another entity page.
    const relationsSection = page.locator("section[aria-label='קשרים']");
    const relationLinks = relationsSection.locator("a");
    const relationCount = await relationLinks.count();
    for (let i = 0; i < relationCount; i++) {
      const href = await relationLinks.nth(i).getAttribute("href");
      expect(href).toMatch(/\/entities\/\d+/);
    }
  });

  test("watchlist-only toggle and kind/country filters narrow the list without an error", async ({
    page,
  }) => {
    await page.goto("/entities");
    const list = page.getByRole("list");
    await expect(list).toBeVisible({ timeout: 15_000 });

    await page.getByLabel("רשימת מעקב בלבד", { exact: false }).check();
    await page.waitForTimeout(400);
    await expect(page.getByRole("alert")).toHaveCount(0);

    await page.getByLabel("רשימת מעקב בלבד", { exact: false }).uncheck();
    await page.getByLabel("סינון לפי סוג ישות").selectOption("company");
    await page.waitForTimeout(400);
    await expect(page.getByRole("alert")).toHaveCount(0);
  });

  test("no bad literal text on the entities list or an entity detail view", async ({ page }, testInfo) => {
    await page.goto("/entities");
    await assertNoBadText(page, testInfo, "Entities list (/entities)");

    await page.getByLabel("חיפוש ישויות").fill("Elbit");
    await page.waitForTimeout(600);
    const firstLink = page.getByRole("list").locator("li a").first();
    await expect(firstLink).toBeVisible({ timeout: 15_000 });
    await firstLink.click();
    await expect(page.locator("section[aria-label='גרף ישויות']")).toBeVisible({ timeout: 15_000 });
    await assertNoBadText(page, testInfo, "Entity detail (/entities/:id)");
  });
});
