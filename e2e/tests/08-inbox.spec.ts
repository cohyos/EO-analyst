import { test, expect } from "./fixtures";
import { assertNoBadText } from "../utils/helpers";

test.describe("Inbox screen (/inbox)", () => {
  test("survey renders its questions, answering a scale question and submitting fires POST /api/surveys/*", async ({
    page,
  }) => {
    await page.goto("/inbox");
    await expect(page.locator("header h1")).toHaveText("הבהרות ומשוב");

    const surveySection = page.locator("section[aria-label='שאלון יומי']");
    await expect(surveySection).toBeVisible({ timeout: 15_000 });

    const noSurvey = surveySection.getByText("אין שאלון זמין");
    if (await noSurvey.count()) {
      test.skip(true, "No survey currently available in this environment");
    }

    const form = surveySection.locator("form");
    await expect(form).toBeVisible();

    // Answer the first scale question found (1..5 circular buttons), then
    // submit and assert the POST fires.
    const scaleGroup = form.locator("div.flex.items-center.gap-2").first();
    const scaleButtons = scaleGroup.locator("button");
    const hasScale = (await scaleButtons.count()) > 0;
    test.skip(!hasScale, "No scale-type question on the current survey");

    await scaleButtons.nth(2).click(); // pick "3"

    const submitBtn = form.getByRole("button", { name: "שלח משוב" });
    const [response] = await Promise.all([
      page.waitForResponse(
        (r) => /\/api\/surveys\/.+\/answers/.test(r.url()) && r.request().method() === "POST",
      ),
      submitBtn.click(),
    ]);
    expect(response.ok()).toBeTruthy();
    await expect(page.getByText("תודה — המשוב נשלח.")).toBeVisible({ timeout: 10_000 });
  });

  test("lessons list renders rows or an explicit empty state", async ({ page }) => {
    await page.goto("/inbox");
    const lessonsSection = page.locator("section[aria-label='מה למדתי ממך']");
    await expect(lessonsSection).toBeVisible({ timeout: 15_000 });

    const empty = lessonsSection.getByText("אין עדיין לקחים שנרשמו");
    const rows = lessonsSection.locator("li");
    await expect(async () => {
      const [emptyVisible, rowCount] = await Promise.all([empty.isVisible().catch(() => false), rows.count()]);
      expect(emptyVisible || rowCount > 0).toBeTruthy();
    }).toPass({ timeout: 15_000 });
  });

  test("clarifications section renders open questions or the explicit empty state", async ({ page }) => {
    await page.goto("/inbox");
    const clarSection = page.locator("section[aria-label='הבהרות פתוחות']");
    await expect(clarSection).toBeVisible({ timeout: 15_000 });

    const empty = clarSection.getByText("אין הבהרות פתוחות");
    const rows = clarSection.locator("li");
    await expect(async () => {
      const [emptyVisible, rowCount] = await Promise.all([empty.isVisible().catch(() => false), rows.count()]);
      expect(emptyVisible || rowCount > 0).toBeTruthy();
    }).toPass({ timeout: 15_000 });
  });

  test("no bad literal text on the inbox screen", async ({ page }, testInfo) => {
    await page.goto("/inbox");
    await expect(page.locator("header h1")).toHaveText("הבהרות ומשוב");
    await assertNoBadText(page, testInfo, "Inbox (/inbox)");
  });
});
