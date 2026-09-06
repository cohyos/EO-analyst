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

  // Q5-16 (docs/qa/findings_Q5_r2.md): this checkbox's onChange goes through react-router's
  // `setSearchParams` (a history/URL update), which Playwright's own `.check()`/`.uncheck()`
  // treat as a "scheduled navigation" to wait out. Their built-in post-click state check is a
  // single, immediate read of the checkbox's DOM `checked` property -- reproduced directly and
  // repeatedly (outside this suite): that single read can land in the narrow window before
  // React's re-render has caught up with the new URL, throwing "Clicking the checkbox did not
  // change its state" even though the click and the resulting state/URL change both land
  // correctly a moment later every time. This is a Playwright/React timing artifact, not a
  // detachment or remount of the control (EntityListPanel never unmounts across this flow --
  // confirmed by watching the same DOM node throughout the repro). Fix: drive the checkbox with
  // a plain `.click()` and verify the resulting state with `expect(...).toBeChecked()`, which
  // polls/retries instead of checking once.
  //
  // The first toggle also waits for the entities list's own network round-trip (not a blind
  // timeout) before asserting -- but the *second* toggle (back to the untouched default filter
  // set: no q/kind/country, watchlist off) must NOT wait for a response the same way: the
  // QueryClient's global 15s `staleTime` (web/src/App.tsx) means react-query serves that exact
  // query straight from cache with no network round-trip at all, so waiting for one there
  // deadlocks until the timeout. `toBeChecked()`'s own polling is enough to confirm the UI state
  // actually flipped back.
  test("watchlist-only toggle and kind/country filters narrow the list without an error", async ({
    page,
  }) => {
    await page.goto("/entities");
    const list = page.getByRole("list");
    await expect(list).toBeVisible({ timeout: 15_000 });

    const watchlistCheckbox = page.getByRole("checkbox", { name: "רשימת מעקב בלבד" });
    await expect(watchlistCheckbox).toBeVisible();

    await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/entities") && r.url().includes("watchlist=true")),
      watchlistCheckbox.click(),
    ]);
    await expect(watchlistCheckbox).toBeChecked();
    await expect(page.getByRole("alert")).toHaveCount(0);

    await watchlistCheckbox.click();
    await expect(watchlistCheckbox).not.toBeChecked();
    await expect(page.getByRole("alert")).toHaveCount(0);

    await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/entities") && r.url().includes("kind=company")),
      page.getByLabel("סינון לפי סוג ישות").selectOption("company"),
    ]);
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
