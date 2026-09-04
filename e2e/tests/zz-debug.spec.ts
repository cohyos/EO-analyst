import { test } from "@playwright/test";
test("debug-settings", async ({ page }) => {
  const resps: string[] = [];
  page.on("response", async (r) => {
    if (r.url().includes("/api/settings/config")) {
      let body = "";
      try { body = (await r.text()).slice(0,500); } catch {}
      resps.push(`${r.status()} ${r.url()} :: ${body}`);
    }
  });
  await page.goto("/settings");
  await page.waitForTimeout(1500);
  const textarea = page.getByLabel("עריכת config.yaml");
  await textarea.waitFor({ timeout: 10000 }).catch(e => console.log("TEXTAREA WAIT ERR", e.message));
  console.log("textarea count", await textarea.count());
  if (await textarea.count()) {
    const val = await textarea.inputValue();
    console.log("VALUE LEN", val.length, val.slice(0,100));
  }
  const saveBtn = page.getByRole("button", { name: "שמור" });
  console.log("save btn count", await saveBtn.count());
  await saveBtn.click();
  await page.waitForTimeout(1500);
  console.log("RESPONSES:", resps);
});
