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
