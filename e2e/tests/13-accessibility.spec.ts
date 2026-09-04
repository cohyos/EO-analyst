import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "./fixtures";

const SCREENS: { path: string; label: string }[] = [
  { path: "/", label: "Morning" },
  { path: "/feed", label: "Feed" },
  { path: "/entities", label: "Entities" },
  { path: "/investigations", label: "Investigations" },
  { path: "/ask", label: "Ask" },
  { path: "/conferences", label: "Conferences" },
  { path: "/inbox", label: "Inbox" },
  { path: "/reports", label: "Reports" },
  { path: "/settings", label: "Settings" },
];

test.describe("Accessibility smoke", () => {
  test("every button and link has an accessible name", async ({ page }) => {
    await page.goto("/feed");
    await expect(page.locator('[data-testid^="feed-row-"]').first()).toBeVisible({ timeout: 20_000 });

    const controls = page.locator("button, a[href]");
    const n = await controls.count();
    const offenders: string[] = [];
    for (let i = 0; i < n; i++) {
      const el = controls.nth(i);
      if (!(await el.isVisible())) continue;
      const accessibleName = (
        (await el.getAttribute("aria-label")) ||
        (await el.getAttribute("title")) ||
        (await el.innerText().catch(() => "")) ||
        ""
      ).trim();
      if (!accessibleName) {
        const outerHtml = await el.evaluate((e) => e.outerHTML.slice(0, 150));
        offenders.push(outerHtml);
      }
    }
    expect(offenders, `Controls with no accessible name:\n${offenders.join("\n")}`).toEqual([]);
  });

  test("focus is visible when tabbing through the nav rail", async ({ page }) => {
    await page.goto("/");
    // Tab from the top of the document into the nav rail's first link.
    await page.keyboard.press("Tab");
    const focused = page.locator(":focus");
    await expect(focused).toBeVisible();
    const outline = await focused.evaluate((el) => {
      const cs = getComputedStyle(el);
      return { outlineStyle: cs.outlineStyle, outlineWidth: cs.outlineWidth, boxShadow: cs.boxShadow };
    });
    const hasVisibleFocusRing =
      (outline.outlineStyle !== "none" && outline.outlineWidth !== "0px") ||
      (outline.boxShadow && outline.boxShadow !== "none");
    expect(hasVisibleFocusRing, `Focused element has no visible focus ring: ${JSON.stringify(outline)}`).toBeTruthy();
  });

  for (const screen of SCREENS) {
    test(`axe-core scan: ${screen.label} (${screen.path}) has no critical violations`, async ({ page }) => {
      await page.goto(screen.path);
      await page.waitForTimeout(1500); // let data-dependent content render before scanning
      const results = await new AxeBuilder({ page }).analyze();

      const critical = results.violations.filter((v) => v.impact === "critical");
      if (critical.length > 0) {
        const details = critical
          .map((v) => `${v.id}: ${v.help} (${v.nodes.length} node(s)) — ${v.nodes[0]?.target.join(" ")}`)
          .join("\n");
        expect(critical, `Critical axe violations on ${screen.path}:\n${details}`).toEqual([]);
      }

      // Non-critical violations are recorded as an attachment for visibility
      // but do not fail the run, per spec ("fail only on 'critical'").
      if (results.violations.length > 0) {
        await test.info().attach(`axe-violations-${screen.label}`, {
          body: JSON.stringify(
            results.violations.map((v) => ({
              id: v.id,
              impact: v.impact,
              help: v.help,
              nodes: v.nodes.length,
            })),
            null,
            2,
          ),
          contentType: "application/json",
        });
      }
    });
  }
});
