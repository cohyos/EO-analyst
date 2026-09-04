import { test, expect } from "./fixtures";
import { recordFinding } from "../utils/helpers";

test.describe("Status strip (persistent footer)", () => {
  test("connects over WS /ws/status and shows service dots + numeric VRAM/RAM readouts", async ({
    page,
  }, testInfo) => {
    const wsCloseCount = { n: 0 };
    page.on("websocket", (ws) => {
      if (!ws.url().includes("/ws/status")) return;
      ws.on("close", () => {
        wsCloseCount.n++;
      });
    });

    await page.goto("/");
    const footer = page.locator("footer");
    await expect(footer).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(5000); // let the WS connect/reconnect settle

    const disconnectedLabel = footer.getByText("מנותק מהשרת");
    const isDisconnected = await disconnectedLabel.isVisible().catch(() => false);
    if (isDisconnected) {
      await recordFinding(page, testInfo, {
        screen: "Status strip (persistent footer)",
        expected: "The status strip connects over WS /ws/status and shows live VRAM/RAM/service-dot readouts",
        actual: `Status strip is stuck showing "מנותק מהשרת — מנסה להתחבר מחדש…"; the WS /ws/status socket opened and closed ${wsCloseCount.n} time(s) during the wait without ever delivering data`,
        severity: "high",
      });
      expect(isDisconnected, "Status strip should not be stuck disconnected").toBeFalsy();
      return;
    }

    const strip = page.getByRole("status", { name: /סטטוס משאבים/ });
    await expect(strip).toBeVisible();

    for (const label of ["PG", "Ollama", "SearXNG", "ntfy"]) {
      await expect(strip.getByText(label, { exact: true })).toBeVisible();
    }

    // VRAM/RAM readouts must be real numbers — this is exactly the kind of
    // screen that produces literal "NaN"/"undefined" text when the
    // frontend's expected status-payload shape drifts from what the
    // backend actually sends.
    const stripText = (await strip.innerText()).trim();
    const badTokens = ["NaN", "undefined", "null"].filter((t) => stripText.includes(t));
    const vramTitle = await strip.locator("div[title^='VRAM']").getAttribute("title").catch(() => null);
    if (vramTitle && /undefined|NaN/.test(vramTitle)) {
      badTokens.push(`title="${vramTitle}"`);
    }

    if (badTokens.length > 0) {
      await recordFinding(page, testInfo, {
        screen: "Status strip (persistent footer)",
        expected: "VRAM/RAM/GPU/disk readouts render real numbers, never NaN/undefined",
        actual: `Bad tokens found in the status strip: ${badTokens.join(", ")}. Full strip text: "${stripText}"`,
        severity: "high",
      });
    }
    expect(badTokens, `Status strip renders bad literal values: ${badTokens.join(", ")}`).toEqual([]);
  });

  test("clicking the strip opens the resource-history drawer (when connected)", async ({ page }, testInfo) => {
    await page.goto("/");
    const footer = page.locator("footer");
    await expect(footer).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(5000);

    const isDisconnected = await footer.getByText("מנותק מהשרת").isVisible().catch(() => false);
    test.skip(isDisconnected, "Status strip is disconnected — see the other test's recorded finding");

    const strip = page.getByRole("status", { name: /סטטוס משאבים/ });
    const toggle = strip.getByTitle("הצג/הסתר היסטוריית משאבים");
    if ((await toggle.count()) === 0) {
      await recordFinding(page, testInfo, {
        screen: "Status strip (persistent footer)",
        expected: 'Clicking the status strip opens a resource-history drawer (a "הצג/הסתר היסטוריית משאבים" toggle wraps the strip contents)',
        actual: "No clickable toggle exists on the status strip in the deployed build — the strip is static, non-interactive text",
        severity: "low",
      });
      test.skip(true, "Resource-history drawer is not present in this build");
    }
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
  });
});
