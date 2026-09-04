import { test, expect } from "./fixtures";

test.describe("Status strip (persistent footer)", () => {
  test("shows service dots and numeric VRAM/RAM readouts", async ({ page }) => {
    await page.goto("/");
    const strip = page.getByRole("status", { name: /סטטוס משאבים/ });
    await expect(strip).toBeVisible({ timeout: 15_000 });

    // Disconnected state renders "מנותק"/WifiOff with no numbers — give the
    // websocket a few seconds to connect before asserting on numeric content.
    await expect(strip.getByText("מנותק מהשרת")).toHaveCount(0, { timeout: 15_000 });

    const vramLabel = strip.getByText("VRAM");
    await expect(vramLabel).toBeVisible();
    const vramPct = strip.locator("text=/\\d+%/").first();
    await expect(vramPct).toBeVisible();
    const pctText = await vramPct.textContent();
    expect(Number(pctText!.replace("%", ""))).not.toBeNaN();

    const gbText = strip.locator("text=/\\d+(\\.\\d+)?\\/\\d+(\\.\\d+)? GB/");
    await expect(gbText.first()).toBeVisible();

    // service dots: postgres/ollama/searxng/ntfy labels each with a colored dot
    for (const label of ["PG", "Ollama", "SearXNG", "ntfy"]) {
      await expect(strip.getByText(label, { exact: true })).toBeVisible();
    }
  });

  test("clicking the strip opens the resource-history drawer", async ({ page }) => {
    await page.goto("/");
    const strip = page.getByRole("status", { name: /סטטוס משאבים/ });
    await expect(strip).toBeVisible({ timeout: 15_000 });
    await expect(strip.getByText("מנותק מהשרת")).toHaveCount(0, { timeout: 15_000 });

    const toggle = strip.getByTitle("הצג/הסתר היסטוריית משאבים");
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
  });
});
