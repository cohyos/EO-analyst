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
    async ({ page }, testInfo) => {
      await page.goto("/settings");
      await page.getByRole("tab", { name: "config", exact: true }).click();
      const textarea = page.getByLabel("עריכת config.yaml");
      await expect(textarea).toBeVisible({ timeout: 15_000 });
      await expect(async () => {
        expect((await textarea.inputValue()).length).toBeGreaterThan(0);
      }).toPass({ timeout: 10_000 });

      // exact: true -- the Models section above also has its own "שמור שרשראות"
      // (ChainsEditor) button; a plain substring match against "שמור" resolves
      // to both once ChainsEditor has rendered (a race the faster/Chromium
      // projects usually win before this line runs, but not guaranteed —
      // this locator should be deterministic regardless of engine/timing).
      const saveBtn = page.getByRole("button", { name: "שמור", exact: true });
      const [response] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().includes("/api/settings/config") && r.request().method() === "PUT",
        ),
        saveBtn.click(),
      ]);

      if (response.status() >= 500) {
        const body = await response.text().catch(() => "(unreadable body)");
        await recordFinding(page, testInfo, {
          screen: "Settings (/settings)",
          expected: "PUT /api/settings/config succeeds (2xx) or returns a client-side validation error (4xx) for a no-op save",
          actual: `Server returned ${response.status()}: ${body}`,
          severity: "critical",
        });
      }
      expect(response.status(), "PUT /api/settings/config should not 5xx on a no-op save").toBeLessThan(500);

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

// U8-ה (ChainsEditor): the fallback-chain editor persists through `PUT /api/llm/settings
// {chains}`, which only exists in code added alongside the editor itself -- the shared live
// instance at BASE_URL (8765 by default) may still be running the pre-chains build. These tests
// therefore target a throwaway instance (a freshly built frontend + a `python -m uvicorn
// eoa.api.app:app --port 8766` process, per docs/MODULES.md's llm section) via
// EOA_CHAINS_BASE_URL, defaulting to http://127.0.0.1:8766 -- never the shared 8765 the rest of
// this file exercises, so a failed/aborted run here can't corrupt the live app's session or an
// in-progress night run.
test.describe("Settings — LLM chain editor (throwaway instance)", () => {
  // Opt-in only: these tests write real settings, so they run solely when a throwaway
  // instance is provided via EOA_CHAINS_BASE_URL (QA r2, 2026-09-06).
  test.skip(!process.env.EOA_CHAINS_BASE_URL, "set EOA_CHAINS_BASE_URL to a throwaway instance to run the chain-editor tests");
  test.use({ baseURL: process.env.EOA_CHAINS_BASE_URL ?? "http://127.0.0.1:8766" });

  // Every test in this block starts from an empty `resident` chain and restores it afterward
  // (direct API call, not through the UI) -- config.yaml's `llm_providers.chains` is a real,
  // shared file, and this suite must never leave test data behind in it.
  test.afterEach(async ({ page }) => {
    await page.request
      .put("/api/llm/settings", {
        data: { chains: { resident: [], investigator: [], light: [], report: [] } },
      })
      .catch(() => undefined);
  });

  test("add step, pick a model and power, save, and reload to confirm it persisted", async ({ page }) => {
    await page.request.put("/api/llm/settings", { data: { chains: { resident: [] } } });
    await page.goto("/settings");

    const chainsSection = page.getByRole("region", { name: "שרשראות נפילה לפי תפקיד" });
    await expect(chainsSection).toBeVisible({ timeout: 15_000 });

    await page.getByRole("tab", { name: /תושב \(resident\)/ }).click();
    await page.getByRole("button", { name: "הוסף שלב" }).click();

    const row = chainsSection.locator("li[aria-label='שלב 1']");
    await expect(row).toBeVisible();

    const providerSelect = row.getByLabel("ספק");
    await providerSelect.selectOption("claude");
    const modelSelect = row.getByLabel("מודל");
    await modelSelect.selectOption("claude-sonnet-5");
    const powerSelect = row.getByLabel("עוצמה");
    await expect(powerSelect).toBeVisible();
    await powerSelect.selectOption("high");

    await expect(page.getByText("שינויים לא נשמרו")).toBeVisible();

    const [response] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/llm/settings") && r.request().method() === "PUT",
      ),
      page.getByRole("button", { name: "שמור שרשראות" }).click(),
    ]);
    expect(response.ok()).toBeTruthy();
    await expect(page.getByText("השרשראות נשמרו בהצלחה")).toBeVisible();

    // Reload from scratch and confirm the step round-tripped through config.yaml, including power.
    await page.reload();
    await page.getByRole("tab", { name: /תושב \(resident\)/ }).click();
    const reloadedRow = page.locator("li[aria-label='שלב 1']");
    await expect(reloadedRow.getByLabel("ספק")).toHaveValue("claude");
    await expect(reloadedRow.getByLabel("מודל")).toHaveValue("claude-sonnet-5");
    await expect(reloadedRow.getByLabel("עוצמה")).toHaveValue("high");

    // The fixed, non-removable local terminal step is always shown after the configured steps.
    await expect(chainsSection.getByText("מקומי (Ollama)")).toBeVisible();
  });

  test("reorder two steps with the move-down button and the new order survives a reload", async ({
    page,
  }) => {
    await page.request.put("/api/llm/settings", {
      data: {
        chains: {
          resident: [
            { provider: "agy", model: "gemini-3.8-flash-medium" },
            { provider: "claude", model: "claude-sonnet-5" },
          ],
        },
      },
    });
    await page.goto("/settings");
    await page.getByRole("tab", { name: /תושב \(resident\)/ }).click();

    const firstRow = page.locator("li[aria-label='שלב 1']");
    await expect(firstRow.getByLabel("ספק")).toHaveValue("agy");

    await firstRow.getByLabel("העבר למטה").click();

    // After moving step 1 down, step 1 should now be the one that used to be step 2 (claude).
    await expect(page.locator("li[aria-label='שלב 1']").getByLabel("ספק")).toHaveValue("claude");
    await expect(page.locator("li[aria-label='שלב 2']").getByLabel("ספק")).toHaveValue("agy");

    const [response] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/llm/settings") && r.request().method() === "PUT",
      ),
      page.getByRole("button", { name: "שמור שרשראות" }).click(),
    ]);
    expect(response.ok()).toBeTruthy();

    await page.reload();
    await page.getByRole("tab", { name: /תושב \(resident\)/ }).click();
    await expect(page.locator("li[aria-label='שלב 1']").getByLabel("ספק")).toHaveValue("claude");
    await expect(page.locator("li[aria-label='שלב 2']").getByLabel("ספק")).toHaveValue("agy");
  });

  test("removing a step and the copy-to-all-roles action both update the draft immediately", async ({
    page,
  }) => {
    await page.request.put("/api/llm/settings", {
      data: { chains: { resident: [{ provider: "agy", model: "gemini-3.8-flash-medium" }] } },
    });
    await page.goto("/settings");
    const chainsSection = page.getByRole("region", { name: "שרשראות נפילה לפי תפקיד" });
    await expect(chainsSection).toBeVisible({ timeout: 15_000 });

    await page.getByRole("tab", { name: /תושב \(resident\)/ }).click();
    await expect(chainsSection.locator("li[aria-label='שלב 1']")).toBeVisible();

    await page.getByRole("button", { name: "העתק לכל התפקידים" }).click();
    await page.getByRole("tab", { name: /חוקר \(investigator\)/ }).click();
    await expect(chainsSection.locator("li[aria-label='שלב 1']").getByLabel("ספק")).toHaveValue("agy");

    await page.getByRole("tab", { name: /תושב \(resident\)/ }).click();
    await chainsSection.locator("li[aria-label='שלב 1']").getByLabel("הסר שלב").click();
    await expect(chainsSection.locator("li[aria-label='שלב 1']")).toHaveCount(0);
    await expect(chainsSection.getByText("מקומי (Ollama)")).toBeVisible();
  });
});
