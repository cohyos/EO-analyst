# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: screens-iphone.spec.ts >> iPhone screenshot + readability audit >> 02-feed — light
- Location: scripts\screens-iphone.spec.ts:344:11

# Error details

```
Error: page.goto: Could not connect to server
Call log:
  - navigating to "http://localhost:5193/feed", waiting until "load"

```

# Test source

```ts
  246 | }
  247 | 
  248 | const SCREENS: Screen[] = [
  249 |   { name: "01-morning", path: "/" },
  250 |   { name: "02-feed", path: "/feed" },
  251 |   { name: "03-item-detail", path: "/items/19384" },
  252 |   { name: "04-entities-list", path: "/entities" },
  253 |   { name: "05-entity-detail", path: "/entities/14" },
  254 |   { name: "06-entities-graph", path: "/entities?view=graph" },
  255 |   { name: "07-investigations-list", path: "/investigations" },
  256 |   { name: "08-investigation-detail", path: "/investigations/288" },
  257 |   {
  258 |     name: "09-ask",
  259 |     path: "/ask",
  260 |   },
  261 |   {
  262 |     name: "10-conferences",
  263 |     path: "/conferences",
  264 |   },
  265 |   { name: "11-tenders", path: "/tenders" },
  266 |   { name: "12-patents", path: "/patents" },
  267 |   { name: "13-payloads", path: "/payloads" },
  268 |   { name: "14-inbox", path: "/inbox" },
  269 |   {
  270 |     name: "15-reports-list",
  271 |     path: "/reports",
  272 |   },
  273 |   {
  274 |     name: "16-reports-monthly",
  275 |     path: "/reports?id=191",
  276 |     waitMs: 800,
  277 |     extraShots: [
  278 |       {
  279 |         suffix: "table",
  280 |         action: async (page) => {
  281 |           const table = page.locator("table").first();
  282 |           if (await table.isVisible().catch(() => false)) {
  283 |             await table.scrollIntoViewIfNeeded().catch(() => {});
  284 |             await page.waitForTimeout(300);
  285 |           }
  286 |         },
  287 |       },
  288 |       {
  289 |         suffix: "heading",
  290 |         action: async (page) => {
  291 |           const heading = page.locator("h1,h2,h3").nth(1);
  292 |           if (await heading.isVisible().catch(() => false)) {
  293 |             await heading.scrollIntoViewIfNeeded().catch(() => {});
  294 |             await page.waitForTimeout(300);
  295 |           }
  296 |         },
  297 |       },
  298 |     ],
  299 |   },
  300 |   {
  301 |     name: "17-reports-weekly",
  302 |     path: "/reports?id=207",
  303 |     waitMs: 800,
  304 |     extraShots: [
  305 |       {
  306 |         suffix: "table",
  307 |         action: async (page) => {
  308 |           const table = page.locator("table").first();
  309 |           if (await table.isVisible().catch(() => false)) {
  310 |             await table.scrollIntoViewIfNeeded().catch(() => {});
  311 |             await page.waitForTimeout(300);
  312 |           }
  313 |         },
  314 |       },
  315 |     ],
  316 |   },
  317 |   { name: "18-bd", path: "/bd" },
  318 |   { name: "19-product-lines", path: "/product-lines" },
  319 |   { name: "20-product-line-detail", path: "/product-lines/targeting_pods" },
  320 |   { name: "21-dossiers-list", path: "/dossiers" },
  321 |   {
  322 |     name: "22-dossier-detail",
  323 |     path: "/dossiers/elbit-systems-spectro-xr",
  324 |     waitMs: 1200,
  325 |     extraShots: [
  326 |       { suffix: "spec", action: (p) => scrollToId(p, "dossier-specifications") },
  327 |       { suffix: "timeline", action: (p) => scrollToId(p, "dossier-timeline") },
  328 |       { suffix: "deals", action: (p) => scrollToId(p, "dossier-deals") },
  329 |       { suffix: "sources", action: (p) => scrollToId(p, "dossier-sources") },
  330 |     ],
  331 |   },
  332 |   {
  333 |     name: "23-dossier-compare",
  334 |     path: "/dossiers/compare?keys=elbit-systems-spectro-xr,lockheed-martin-sniper-advanced-targeting-pod,rafael-advanced-defense-systems-litening-5",
  335 |     waitMs: 1200,
  336 |   },
  337 |   { name: "24-tech-radar", path: "/tech-radar" },
  338 |   { name: "25-settings", path: "/settings" },
  339 | ];
  340 | 
  341 | test.describe("iPhone screenshot + readability audit", () => {
  342 |   for (const theme of ["light", "dark"] as const) {
  343 |     for (const screen of SCREENS) {
  344 |       test(`${screen.name} — ${theme}`, async ({ page }, testInfo) => {
  345 |         fs.mkdirSync(OUT_DIR, { recursive: true });
> 346 |         await page.goto(screen.path);
      |                    ^ Error: page.goto: Could not connect to server
  347 |         await setTheme(page, theme);
  348 |         await page.reload();
  349 |         await page.waitForTimeout(1200 + (screen.waitMs ?? 0));
  350 | 
  351 |         // The Ask-the-analyst round trip is expensive (up to 60s) -- only exercise it once
  352 |         // per engine (light theme pass) to keep total runtime sane; dark theme still captures
  353 |         // the page's empty/input state, which is enough to judge input/label readability.
  354 |         if (screen.name === "09-ask" && theme === "light") {
  355 |           const input = page.getByPlaceholder("שאל שאלה…");
  356 |           if (await input.isVisible().catch(() => false)) {
  357 |             await input.fill("מה חדש באלביט?");
  358 |             await input.press("Enter");
  359 |             await page.waitForTimeout(60_000).catch(() => {});
  360 |           }
  361 |         }
  362 | 
  363 |         const base = `${screen.name}_${testInfo.project.name}_${theme}`;
  364 |         await shoot(page, base);
  365 |         await writeChecks(page, base);
  366 | 
  367 |         if (screen.extraShots) {
  368 |           for (const extra of screen.extraShots) {
  369 |             await extra.action(page);
  370 |             await shoot(page, `${base}_${extra.suffix}`);
  371 |           }
  372 |         }
  373 | 
  374 |         if (screen.action) {
  375 |           await screen.action(page);
  376 |         }
  377 |       });
  378 |     }
  379 |   }
  380 | });
  381 | 
```