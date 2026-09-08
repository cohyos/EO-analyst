import { test, expect } from "./fixtures";
import { assertNoBadText } from "../utils/helpers";

const API_BASE = process.env.EOA_BASE_URL ?? "http://127.0.0.1:8765";

// PD-ui (docs/PLAN_PRODUCT_DOSSIER.md): "סקירות מוצר" -- per-product deep market dossiers, built
// against the frozen contract in section 5. The five endpoints (`GET /api/dossiers`,
// `POST /api/dossiers`, `GET /api/dossiers/{key}`, `GET /api/dossiers/{key}/{id}`,
// `POST /api/dossiers/{key}/rerun`) may not exist on the live backend this suite runs against yet
// -- every data-dependent test here checks the list endpoint first and `test.skip`s if it 404s,
// exactly like `20-product-lines.spec.ts` skips when PL-ui's own endpoints aren't live. The
// nav/header/form-validation tests below need no backend at all and always run.
test.describe("Product dossiers screen (/dossiers) -- PD-ui", () => {
  test("header renders", async ({ page }) => {
    await page.goto("/dossiers");
    await expect(page.locator("header h1")).toHaveText("סקירות מוצר");
  });

  test("the nav rail links to /dossiers next to קווי מוצר", async ({ page }) => {
    await page.goto("/product-lines");
    const link = page.getByRole("link", { name: "סקירות מוצר" });
    await expect(link).toBeVisible();
    await link.click();
    await expect(page).toHaveURL(/\/dossiers$/);
  });

  test("the 'סקירה חדשה' form validates the required product name before submitting", async ({ page }) => {
    await page.goto("/dossiers");
    await page.getByTestId("dossier-new-button").click();
    await expect(page.getByRole("heading", { name: "סקירת מוצר חדשה" })).toBeVisible();

    await page.getByTestId("dossier-form-submit").click();
    await expect(page.getByRole("alert")).toHaveText("שם המוצר הוא שדה חובה");

    // Typing a name clears the error and the field is no longer marked invalid.
    await page.getByLabel("שם המוצר").fill("SPECTRO XR");
    await expect(page.getByLabel("שם המוצר")).not.toHaveAttribute("aria-invalid", "true");
  });

  test("the 'סקירה חדשה' form accepts alias chips via Enter/comma", async ({ page }) => {
    await page.goto("/dossiers");
    await page.getByTestId("dossier-new-button").click();

    const aliasInput = page.getByLabel("כינויים נוספים");
    await aliasInput.fill("Spectro");
    await aliasInput.press("Enter");
    await expect(page.getByText("Spectro", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "הסר כינוי Spectro" }).click();
    await expect(page.getByText("Spectro", { exact: true })).toHaveCount(0);
  });

  test("GET /api/dossiers responds and the page renders cards or the empty state, never a blank screen", async ({
    page,
    request,
  }) => {
    const res = await request.get(`${API_BASE}/api/dossiers`);
    test.skip(res.status() === 404, "GET /api/dossiers not implemented on this backend yet");
    expect(res.ok()).toBe(true);
    const body = await res.json();
    expect(Array.isArray(body)).toBe(true);

    await page.goto("/dossiers");
    const cards = page.locator('[data-testid^="dossier-card-"]');
    const empty = page.getByText("לא נמצאו סקירות מוצר");
    await expect(async () => {
      const hasCards = (await cards.count()) > 0;
      const hasEmpty = (await empty.count()) > 0;
      expect(hasCards || hasEmpty).toBe(true);
    }).toPass({ timeout: 15_000 });
  });

  test("opening a dossier's detail page shows its fixed section headings", async ({ page, request }) => {
    const res = await request.get(`${API_BASE}/api/dossiers`);
    test.skip(res.status() === 404, "GET /api/dossiers not implemented on this backend yet");
    const body = await res.json();
    test.skip(!Array.isArray(body) || body.length === 0, "No dossiers returned by this backend");

    const first = body[0];
    await page.goto(`/dossiers/${first.product_key}`);
    await expect(page.getByRole("heading", { name: "תקציר", level: 3 })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("heading", { name: "מפרט", level: 3 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "מקורות", level: 3 })).toBeVisible();
  });

  test("no bad literal text on the dossiers screen", async ({ page }, testInfo) => {
    await page.goto("/dossiers");
    await expect(page.locator("header h1")).toHaveText("סקירות מוצר");
    await assertNoBadText(page, testInfo, "Product dossiers (/dossiers)");
  });
});

// PD-vocab-ui (2026-09-09, docs/PLAN_SPEC_VOCABULARY.md §5.1/§5.2): the grouped spec vocabulary
// table and the /dossiers/compare comparison page. Same live-backend-optional discipline as the
// suite above -- the route-level/validation tests need no backend and always run; anything that
// reads real dossier content skips when the backend doesn't have enough data yet (the vocabulary
// extraction lane, PD-vocab-extract, may not have shipped `key`-tagged rows on this backend yet --
// the grouped table still renders correctly against legacy free-named rows, see
// DossierSpecTable's own "tolerant of both shapes" design).
test.describe("Spec vocabulary grouping + comparison (/dossiers/compare) -- PD-vocab-ui", () => {
  test("/dossiers/compare rejects a keys list outside the 2-3 range, no backend required", async ({ page }) => {
    await page.goto("/dossiers/compare?keys=only-one-key");
    await expect(page.getByText("יש לבחור בין 2 ל-3 מוצרים להשוואה")).toBeVisible();
    await expect(page.getByRole("link", { name: /חזרה לרשימת הסקירות/ })).toBeVisible();
  });

  test("dossier detail page's specifications section renders as a grouped table with group headings", async ({
    page,
    request,
  }) => {
    const res = await request.get(`${API_BASE}/api/dossiers`);
    test.skip(res.status() === 404, "GET /api/dossiers not implemented on this backend yet");
    const body = await res.json();
    test.skip(!Array.isArray(body) || body.length === 0, "No dossiers returned by this backend");

    const first = body[0];
    await page.goto(`/dossiers/${first.product_key}`);
    await expect(page.getByRole("heading", { name: "מפרט", level: 3 })).toBeVisible({ timeout: 15_000 });
    // The common vocabulary always carries required rows (e.g. weight/detector type), so the
    // specifications section renders at least one of the 8 fixed group_he headings even for a
    // dossier with a null product_line and zero grounded specification rows.
    const groupHeadings = page.locator("h4").filter({
      hasText: /^(אופטיקה|חיישנים|לייזר|ייצוב ובקרה|מכניקה וסביבה|ממשקים|ביצועי מערכת|בשלות ולוגיסטיקה|פרמטרים נוספים)$/,
    });
    await expect(groupHeadings.first()).toBeVisible({ timeout: 15_000 });
  });

  test("selecting 2 dossiers from the list and comparing opens the comparison page", async ({ page, request }) => {
    const res = await request.get(`${API_BASE}/api/dossiers`);
    test.skip(res.status() === 404, "GET /api/dossiers not implemented on this backend yet");
    const body = await res.json();
    test.skip(!Array.isArray(body) || body.length < 2, "Need at least 2 dossiers to exercise comparison");

    await page.goto("/dossiers");
    const checkboxes = page.locator('[data-testid^="dossier-compare-checkbox-"]');
    await expect(checkboxes.first()).toBeVisible({ timeout: 15_000 });
    await checkboxes.nth(0).check();
    await checkboxes.nth(1).check();
    await page.getByTestId("dossier-compare-selected-button").click();

    await expect(page).toHaveURL(/\/dossiers\/compare\?keys=/);
    await expect(page.getByRole("heading", { name: "השוואת מוצרים" })).toBeVisible({ timeout: 15_000 });
    // Either the grouped comparison renders, or (real dossiers rarely share a product_line yet,
    // since PD-vocab-extract's product_line plumbing may not be live) the honest mismatch warning
    // does -- either is a correct, non-crashing outcome for arbitrary live data.
    const comparisonRendered = page.getByText("קו מוצר:");
    const mismatchWarning = page.getByText("המוצרים שנבחרו אינם מאותו קו מוצר");
    await expect(comparisonRendered.or(mismatchWarning)).toBeVisible({ timeout: 15_000 });
  });

  test("no bad literal text on the comparison page's invalid-selection state", async ({ page }, testInfo) => {
    await page.goto("/dossiers/compare?keys=a,b,c,d,e");
    await expect(page.getByText("יש לבחור בין 2 ל-3 מוצרים להשוואה")).toBeVisible();
    await assertNoBadText(page, testInfo, "Dossier comparison (/dossiers/compare)");
  });
});
