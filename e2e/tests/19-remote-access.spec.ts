import { test, expect } from "./fixtures";

/**
 * ADR-008 (docs/adr/008-remote-access.md): the `AccessGate` (web/src/components/auth/AccessGate.tsx)
 * renders instead of the app whenever the API answers with 401 `{"error":{"code":"auth_required"}}`
 * -- exactly what a non-loopback client without a session gets from
 * `agent/eoa/api/auth.py::RemoteAccessMiddleware`.
 *
 * This suite runs against the live app over `127.0.0.1` (see playwright.config.ts), which is
 * always loopback-trusted regardless of `api.remote_access.enabled` -- there is no way to make a
 * real request here look non-loopback. So, per the task spec, this simulates the *symptom* (a 401
 * auth_required response) via Playwright route mocking rather than the underlying network
 * condition; the middleware's own loopback/non-loopback decision is covered directly by
 * `tests/unit/test_remote_access_auth.py` (TestClient with a `client=("100.70.157.25", 5000)`
 * scope override).
 */

const AUTH_REQUIRED_BODY = {
  error: { code: "auth_required", message_he: "נדרש להתחבר כדי לגשת למערכת מרחוק", detail: null },
};

test.describe("Remote-access gate (ADR-008)", () => {
  test("a 401 auth_required response replaces the app with the passcode gate", async ({ page }) => {
    await page.route("**/api/**", async (route) => {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify(AUTH_REQUIRED_BODY),
      });
    });

    await page.goto("/");

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible({ timeout: 15_000 });
    await expect(dialog.getByText("נדרשת התחברות")).toBeVisible();
    await expect(page.getByLabel("קוד גישה")).toBeVisible();

    // The rest of the app must not be mounted underneath the gate.
    await expect(page.locator("nav")).toHaveCount(0);
  });

  test("submit is disabled until a passcode is typed", async ({ page }) => {
    await page.route("**/api/**", async (route) => {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify(AUTH_REQUIRED_BODY),
      });
    });

    await page.goto("/");
    const submit = page.getByRole("button", { name: "התחבר" });
    await expect(submit).toBeVisible({ timeout: 15_000 });
    await expect(submit).toBeDisabled();

    await page.getByLabel("קוד גישה").fill("x");
    await expect(submit).toBeEnabled();
  });

  test("a wrong passcode shows an inline error and keeps the gate up", async ({ page }) => {
    await page.route("**/api/**", async (route) => {
      if (route.request().url().includes("/api/auth/login")) {
        await route.fulfill({
          status: 401,
          contentType: "application/json",
          body: JSON.stringify({
            error: { code: "invalid_passcode", message_he: "קוד גישה שגוי", detail: null },
          }),
        });
        return;
      }
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify(AUTH_REQUIRED_BODY),
      });
    });

    await page.goto("/");
    await expect(page.getByRole("dialog")).toBeVisible({ timeout: 15_000 });

    await page.getByLabel("קוד גישה").fill("wrong-passcode");
    await page.getByRole("button", { name: "התחבר" }).click();

    await expect(page.getByRole("alert")).toHaveText("קוד גישה שגוי");
    await expect(page.getByRole("dialog")).toBeVisible();
  });

  test("a correct passcode logs in and the real app takes over", async ({ page }) => {
    // Blanket 401 until login succeeds (simulating a logged-out remote client), then let every
    // subsequent request through to the real, live backend -- which has remote_access.enabled
    // false by default, so it just answers normally once the gate is out of the way.
    let loggedIn = false;
    await page.route("**/api/**", async (route) => {
      const url = route.request().url();
      if (loggedIn) {
        await route.continue();
        return;
      }
      if (url.includes("/api/auth/login")) {
        loggedIn = true;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ok: true }),
        });
        return;
      }
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify(AUTH_REQUIRED_BODY),
      });
    });

    await page.goto("/");
    await expect(page.getByRole("dialog")).toBeVisible({ timeout: 15_000 });

    await page.getByLabel("קוד גישה").fill("whatever-the-mock-accepts");
    await page.getByRole("button", { name: "התחבר" }).click();

    await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });
    await expect(page.locator("nav")).toBeVisible();
  });
});
