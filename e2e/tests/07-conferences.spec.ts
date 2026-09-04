import { test, expect } from "./fixtures";
import { assertNoBadText, recordFinding } from "../utils/helpers";

test.describe("Conferences screen (/conferences)", () => {
  test("table has at least 10 rows", async ({ page }) => {
    await page.goto("/conferences");
    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible({ timeout: 15_000 });
    // Rows are expandable in the current source (aria-expanded on each
    // summary row) — but count plain <tr> as the source of truth for "how
    // many conferences render", since an older deployed build may not have
    // the expand/collapse feature at all.
    const n = await rows.count();
    expect(n).toBeGreaterThanOrEqual(10);
  });

  test('each row has an outbound link (href http) or a clearly labeled "אין קישור" — never a silent blank', async ({
    page,
  }, testInfo) => {
    await page.goto("/conferences");
    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible({ timeout: 15_000 });
    const n = await rows.count();

    // The name cell's column index isn't stable across builds (an older
    // build had no leading chevron column; the current source
    // (ConferencesPage.tsx) puts the name in td:nth(1), after the chevron
    // toggle in td:nth(0)). Read the title from whatever holds it instead
    // of hard-coding a column: the row's outbound link (its text is the
    // conference name, per ConferencesPage.tsx's `<a>...<bdi>{c.name}</bdi>`)
    // when present, else the first <bdi> in the row (the plain-text name
    // fallback when there's no outUrl).
    const problems: string[] = [];
    for (let i = 0; i < n; i++) {
      const row = rows.nth(i);
      const link = row.locator("a");
      const hasLink = (await link.count()) > 0;
      if (hasLink) {
        const href = await link.getAttribute("href");
        if (!href || !/^https?:\/\//.test(href)) {
          const name = (await link.innerText()).trim();
          problems.push(`row ${i} ("${name}"): link present but href is not http(s): "${href}"`);
        }
        continue;
      }
      const noLinkLabel = row.getByText("אין קישור");
      if ((await noLinkLabel.count()) === 0) {
        const name = (await row.locator("bdi").first().innerText()).trim();
        problems.push(`row ${i} ("${name}"): no outbound link AND no "אין קישור" label`);
      }
    }

    if (problems.length > 0) {
      await recordFinding(page, testInfo, {
        screen: "Conferences (/conferences)",
        expected: 'Each row shows an outbound link (href http) or an explicit "אין קישור" label',
        actual: `${problems.length}/${n} row(s) have neither: ${problems.slice(0, 5).join("; ")}`,
        severity: "medium",
      });
    }
    expect(problems, problems.join("\n")).toEqual([]);
  });

  test("clicking a row expands details (if the feature exists); clicking again collapses", async ({
    page,
  }, testInfo) => {
    await page.goto("/conferences");
    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible({ timeout: 15_000 });

    const expandableRow = page.locator("tbody tr[aria-expanded]").first();
    if ((await expandableRow.count()) === 0) {
      await recordFinding(page, testInfo, {
        screen: "Conferences (/conferences)",
        expected: "Clicking a conference row expands a details panel (thresholds/changes/entry conditions)",
        actual: 'Deployed build\'s table rows carry no "aria-expanded" / click-to-expand behavior at all — rows are static text only',
        severity: "low",
      });
      test.skip(true, "Row expand/collapse is not present in this build");
    }

    await expect(expandableRow).toHaveAttribute("aria-expanded", "false");
    await expandableRow.click();
    await expect(expandableRow).toHaveAttribute("aria-expanded", "true");
    await expandableRow.click();
    await expect(expandableRow).toHaveAttribute("aria-expanded", "false");
  });

  test("iCal export (top-level) downloads text/calendar", async ({ page }) => {
    await page.goto("/conferences");
    await expect(page.locator("tbody tr").first()).toBeVisible({ timeout: 15_000 });

    const exportLink = page.getByRole("link", { name: /ייצוא iCal/ });
    await expect(exportLink).toBeVisible();

    const [response] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/conferences/ical")),
      exportLink.click(),
    ]);
    expect(response.status()).toBe(200);
    expect(response.headers()["content-type"] ?? "").toContain("text/calendar");
  });

  test('per-row "הוסף ליומן" triggers a real .ics file download (if the control exists)', async ({
    page,
  }, testInfo) => {
    await page.goto("/conferences");
    await expect(page.locator("tbody tr").first()).toBeVisible({ timeout: 15_000 });

    const addToCalBtn = page.locator("tbody").getByTitle("הוסף ליומן (ICS)").first();
    if ((await addToCalBtn.count()) === 0) {
      await recordFinding(page, testInfo, {
        screen: "Conferences (/conferences)",
        expected: 'Each conference row has a "הוסף ליומן" (add-to-calendar / per-conference ICS) control',
        actual: "No per-row add-to-calendar control found in the deployed build (only the top-level bulk export exists)",
        severity: "low",
      });
      test.skip(true, "Per-row calendar export is not present in this build");
    }

    const [download] = await Promise.all([
      page.waitForEvent("download", { timeout: 10_000 }),
      addToCalBtn.click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/\.ics$/);
  });

  test("no bad literal text on the conferences screen", async ({ page }, testInfo) => {
    await page.goto("/conferences");
    await expect(page.locator("tbody tr").first()).toBeVisible({ timeout: 15_000 });
    await assertNoBadText(page, testInfo, "Conferences (/conferences)");
  });
});
