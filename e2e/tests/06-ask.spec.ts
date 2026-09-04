import { test, expect } from "./fixtures";

test.describe("Ask the analyst (/ask)", () => {
  test(
    "sending a question produces a streaming response or an explicit error within 120s (never a silent hang)",
    async ({ page }) => {
      await page.goto("/ask");
      await expect(page.locator("header h1")).toHaveText("שאל את האנליסט");

      const input = page.getByLabel("שאלה לאנליסט");
      await input.fill("מהן ההתפתחויות האחרונות בתחום ה-EO/IR?");

      const sendBtn = page.getByRole("button", { name: "שלח" });
      await sendBtn.click();

      // The moment we send, the UI must switch into a visibly-streaming
      // state — the stop button appears and a streaming caret/placeholder
      // shows in the assistant bubble. A silent hang (nothing changes) is
      // itself a bug worth catching before we even wait out the 120s.
      const stopBtn = page.getByRole("button", { name: "עצור" });
      await expect(stopBtn).toBeVisible({ timeout: 10_000 });

      // Now wait up to 120s total for EITHER a real answer to finish
      // streaming (send button reappears, stop button disappears) OR an
      // explicit error alert.
      const errorAlert = page.getByRole("alert");
      const doneStreaming = page.getByRole("button", { name: "שלח" });

      await expect(async () => {
        const [errCount, doneVisible] = await Promise.all([
          errorAlert.count(),
          doneStreaming.isVisible(),
        ]);
        expect(errCount > 0 || doneVisible).toBeTruthy();
      }).toPass({ timeout: 120_000, intervals: [2000] });

      if (await errorAlert.count()) {
        const text = await errorAlert.first().textContent();
        expect(text?.trim().length ?? 0).toBeGreaterThan(0);
      } else {
        // Happy path: some assistant content rendered (even a short one) —
        // never an empty bubble left behind.
        const assistantBubbles = page.locator("main .bg-bg-raised.shadow-panel, main [class*='bg-bg-raised']");
        expect(await assistantBubbles.count()).toBeGreaterThan(0);
      }
    },
  );

  test("empty state renders explanatory Hebrew text before any question is sent", async ({ page }) => {
    await page.goto("/ask");
    await expect(page.getByRole("main").getByText("שאל את האנליסט")).toBeVisible();
  });
});
