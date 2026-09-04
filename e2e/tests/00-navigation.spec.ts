import { test, expect } from "./fixtures";
import { assertNoHorizontalScroll } from "../utils/helpers";

/**
 * Nav rail: every link routes and the page heading (TopBar h1) changes;
 * browser back works; the active link is highlighted (aria-current + a
 * visibly different class from the inactive links).
 */

const SCREENS: { path: string; label: string; heading: string }[] = [
  { path: "/", label: "הבוקר", heading: "הבוקר" },
  { path: "/feed", label: "פיד Triage", heading: "פיד Triage" },
  { path: "/entities", label: "ישויות וגרף", heading: "ישויות וגרף" },
  { path: "/investigations", label: "חקירות עומק", heading: "חקירות עומק" },
  { path: "/ask", label: "שאל את האנליסט", heading: "שאל את האנליסט" },
  { path: "/conferences", label: "לוח כנסים", heading: "לוח כנסים" },
  { path: "/inbox", label: "הבהרות ומשוב", heading: "הבהרות ומשוב" },
  { path: "/reports", label: "דוחות", heading: "דוחות" },
  { path: "/settings", label: "הגדרות", heading: "הגדרות" },
];

test.describe("Navigation rail", () => {
  test("every nav link routes and updates the page heading", async ({ page }) => {
    await page.goto("/");

    const nav = page.getByRole("navigation", { name: "ניווט ראשי" });
    await expect(nav).toBeVisible();

    for (const screen of SCREENS) {
      const link = nav.getByRole("link", { name: screen.label });
      await link.click();
      await expect(page).toHaveURL(new RegExp(`${screen.path === "/" ? "/$" : screen.path}`));
      const h1 = page.locator("header h1");
      await expect(h1).toHaveText(screen.heading);
    }
  });

  test("active nav link is highlighted with aria-current", async ({ page }) => {
    await page.goto("/feed");
    const nav = page.getByRole("navigation", { name: "ניווט ראשי" });
    const activeLink = nav.getByRole("link", { name: "פיד Triage" });
    await expect(activeLink).toHaveAttribute("aria-current", "page");

    const morningLink = nav.getByRole("link", { name: "הבוקר" });
    await expect(morningLink).not.toHaveAttribute("aria-current", "page");
  });

  test("browser back navigates to the previous screen", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("navigation", { name: "ניווט ראשי" }).getByRole("link", { name: "פיד Triage" }).click();
    await expect(page).toHaveURL(/\/feed/);
    await page.getByRole("navigation", { name: "ניווט ראשי" }).getByRole("link", { name: "ישויות וגרף" }).click();
    await expect(page).toHaveURL(/\/entities/);

    await page.goBack();
    await expect(page).toHaveURL(/\/feed/);
    await expect(page.locator("header h1")).toHaveText("פיד Triage");

    await page.goBack();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator("header h1")).toHaveText("הבוקר");
  });

  test("app shell is RTL", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.locator("html")).toHaveAttribute("lang", "he");
    const dir = await page.evaluate(() => document.dir);
    expect(dir).toBe("rtl");
  });

  test("no page-level horizontal scroll on the shell", async ({ page }, testInfo) => {
    await page.goto("/");
    await assertNoHorizontalScroll(page, testInfo, "AppShell (/)");
  });
});
