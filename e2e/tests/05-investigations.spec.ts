import { test, expect } from "./fixtures";
import { assertNoBadText } from "../utils/helpers";

const API_BASE = process.env.EOA_BASE_URL ?? "http://127.0.0.1:8765";

test.describe("Investigations screens (/investigations, /investigations/:jobId)", () => {
  test("table rows render for each investigation", async ({ page, request }) => {
    const list = await (await request.get(`${API_BASE}/api/investigations?limit=30`)).json();
    test.skip(!Array.isArray(list) || list.length === 0, "No investigations exist in this environment");

    await page.goto("/investigations");
    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible({ timeout: 15_000 });
    expect(await rows.count()).toBeGreaterThan(0);
  });

  test("detail page shows log rows and the final answer (when done), stop button while running", async ({
    page,
    request,
  }) => {
    const list = (await (await request.get(`${API_BASE}/api/investigations?limit=30`)).json()) as Array<{
      job_id: number;
      state: string;
    }>;
    test.skip(!Array.isArray(list) || list.length === 0, "No investigations exist in this environment");

    // Prefer a running job so the stop button is exercised; fall back to
    // whatever is first.
    const running = list.find((j) => j.state === "running");
    const target = running ?? list[0];

    await page.goto(`/investigations/${target.job_id}`);
    await expect(page.locator("header h1")).toHaveText("חקירות עומק");

    const logSection = page.locator("section[aria-label='לוג חקירה חי']");
    await expect(logSection).toBeVisible({ timeout: 15_000 });

    const noLogEmptyState = logSection.getByText("אין עדיין רשומות לוג");
    if (await noLogEmptyState.count()) {
      await expect(noLogEmptyState).toBeVisible();
    } else {
      const logItems = logSection.locator("ol li");
      expect(await logItems.count()).toBeGreaterThan(0);
    }

    if (running) {
      await expect(page.getByRole("button", { name: "עצור" })).toBeVisible();
    }

    const detail = await (await request.get(`${API_BASE}/api/investigations/${target.job_id}`)).json();
    if (detail.answer) {
      await expect(page.locator("section[aria-label='תשובה סופית']")).toBeVisible();
    }
  });

  test("no bad literal text on investigations list/detail", async ({ page, request }, testInfo) => {
    await page.goto("/investigations");
    await assertNoBadText(page, testInfo, "Investigations list (/investigations)");

    const list = (await (await request.get(`${API_BASE}/api/investigations?limit=1`)).json()) as Array<{
      job_id: number;
    }>;
    test.skip(!Array.isArray(list) || list.length === 0, "No investigations exist in this environment");
    await page.goto(`/investigations/${list[0].job_id}`);
    await expect(page.locator("header h1")).toHaveText("חקירות עומק");
    await assertNoBadText(page, testInfo, "Investigation detail (/investigations/:jobId)");
  });
});
