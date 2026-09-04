import { test } from "@playwright/test";
test("debug-status4", async ({ page }) => {
  await page.goto("/");
  await page.waitForTimeout(5000);
  const count = await page.getByRole("status", { name: /סטטוס משאבים/ }).count();
  console.log("STATUS ROLE COUNT:", count);
  const toggleCount = await page.getByTitle("הצג/הסתר היסטוריית משאבים").count();
  console.log("TOGGLE COUNT (page-wide):", toggleCount);
});
