import { test, expect } from "./fixtures";
import { assertNoBadText, recordFinding } from "../utils/helpers";

const TABS = ["config", "sources", "watchlist", "taxonomy", "models"];

test.describe("Settings screen (/settings)", () => {
  test("each YAML tab loads content", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.locator("header h1")).toHaveText("הגדרות");

    for (const tab of TABS) {
      await page.getByRole("tab", { name: tab, exact: true }).click();
      const textarea = page.getByLabel(`עריכת ${tab}.yaml`);
      await expect(textarea).toBeVisible({ timeout: 15_000 });
      await expect(async () => {
        const value = await textarea.inputValue();
        expect(value.length, `${tab}.yaml should not be empty`).toBeGreaterThan(0);
      }).toPass({ timeout: 10_000 });
    }
  });

  test(
    "a no-op save (writing the same YAML back) fires PUT and shows success or a validation error",
    async ({ page }) => {
      await page.goto("/settings");
      await page.getByRole("tab", { name: "config", exact: true }).click();
      const textarea = page.getByLabel("עריכת config.yaml");
      await expect(textarea).toBeVisible({ timeout: 15_000 });
      await expect(async () => {
        expect((await textarea.inputValue()).length).toBeGreaterThan(0);
      }).toPass({ timeout: 10_000 });

      const saveBtn = page.getByRole("button", { name: "שמור" });
      const [response] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().includes("/api/settings/config") && r.request().method() === "PUT",
        ),
        saveBtn.click(),
      ]);
      expect(response.status()).toBeLessThan(500);

      const success = page.getByText("נשמר בהצלחה");
      const failure = page.getByText(/^שגיאות:/);
      await expect(async () => {
        const [ok, bad] = await Promise.all([success.isVisible().catch(() => false), failure.isVisible().catch(() => false)]);
        expect(ok || bad).toBeTruthy();
      }).toPass({ timeout: 10_000 });
    },
  );

  test("jobs table renders rows or an explicit empty state, and mode toggle switches", async ({ page }) => {
    await page.goto("/settings");
    const jobsSection = page.locator("section[aria-label='עבודות (Jobs)']");
    await expect(jobsSection).toBeVisible({ timeout: 15_000 });

    const empty = jobsSection.getByText("אין עבודות");
    const rows = jobsSection.locator("tbody tr");
    await expect(async () => {
      const [emptyVisible, rowCount] = await Promise.all([empty.isVisible().catch(() => false), rows.count()]);
      expect(emptyVisible || rowCount > 0).toBeTruthy();
    }).toPass({ timeout: 15_000 });

    const ecoBtn = page.getByRole("button", { name: "מצב חסכוני" });
    const fullBtn = page.getByRole("button", { name: "מצב מלא" });
    await ecoBtn.click();
    await expect(page.getByRole("button", { name: /הרץ ריצה יומית \(eco\)/ })).toBeVisible();
    await fullBtn.click();
    await expect(page.getByRole("button", { name: /הרץ ריצה יומית \(full\)/ })).toBeVisible();
  });

  test("no bad literal text on the settings screen", async ({ page }, testInfo) => {
    await page.goto("/settings");
    await expect(page.locator("header h1")).toHaveText("הגדרות");
    await assertNoBadText(page, testInfo, "Settings (/settings)");
  });
});
