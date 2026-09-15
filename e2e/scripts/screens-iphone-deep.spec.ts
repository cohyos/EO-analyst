import { test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * iPhone/mobile-web readability audit — ROUND 3 ("deep pass", 2026-09-15). Round 1's method
 * (`screens-iphone.spec.ts`) only ever screenshotted the FIRST viewport of `<main>` (the app
 * scrolls *inside* `<main class="… overflow-y-auto">`, not the document) and never opened a
 * single interactive state. This spec:
 *
 *   A. Scrolls every route's `<main>` in steps of (clientHeight - 80px), screenshotting +
 *      running readability checks at every step (capped at 12 steps/route).
 *   B. Opens every interactive state called out in the round-3 brief (sheets, menus, popovers,
 *      forms, the chat panel with a real question) and screenshots each open state.
 *   C. Runs a programmatic sweep (activeElement visibility, off-screen elements, >40%-coverage
 *      fixed elements, inner/outer horizontal & vertical overflow) on every screenshot, in both
 *      sections.
 *
 * Deliberately outside e2e/tests/ (not part of the pass/fail QA suite) — same reasoning as
 * `screens-iphone.spec.ts` and `screens-tablet.spec.ts`. Screenshots + checks JSON go OUTSIDE the
 * repo, to the audit scratchpad; see docs/qa/content_review/UI-MOBILE-iphone-r3.md for the report
 * built from this pass's output.
 *
 * Run with:
 *
 *   cd e2e && npx playwright test --config=scripts/pw.iphone-shots.config.ts \
 *     --project=iphone-safari --grep-invert @skip
 *
 * (Reuses `pw.iphone-shots.config.ts`, which testMatch's `screens-iphone.spec.ts` only by name —
 * point `testMatch` at this file instead, or pass this file's basename via `--config` override;
 * see the run command captured in the final report.)
 */

const OUT_DIR =
  process.env.IPHONE_AUDIT_OUT_DIR ??
  "C:\\Users\\cohyo\\AppData\\Local\\Temp\\claude\\C--Users-cohyo-Documents-EO-analyst\\3b24a26d-4bdd-49a3-b2dd-f67f85df545c\\scratchpad\\iphone-audit-r3";

test.beforeEach(async ({ page }) => {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  // Hard rule: never let a share button actually open mail/WhatsApp — kill any popup on sight.
  page.on("popup", (p) => {
    p.close().catch(() => {});
  });
});

async function setTheme(page: Page, theme: "dark" | "light") {
  await page.evaluate((t) => {
    try {
      const raw = localStorage.getItem("eo-analyst-ui");
      const parsed = raw ? JSON.parse(raw) : { state: {}, version: 0 };
      parsed.state = { ...parsed.state, theme: t, locale: parsed.state?.locale ?? "he" };
      localStorage.setItem("eo-analyst-ui", JSON.stringify(parsed));
    } catch {
      /* best-effort */
    }
    document.documentElement.dataset.theme = t;
  }, theme);
}

/**
 * Runs entirely inside the page — self-contained (page.evaluate semantics). Superset of
 * `screens-iphone.spec.ts`'s `collectChecks`: adds (i) text-node overlap, (ii) ancestor-overflow
 * clipping, (iii) oversize images/canvases, (iv) activeElement visibility, (v) >40%-viewport
 * fixed/sticky coverage, (vi) inner (`main`) horizontal overflow, (vii) phantom outer/document
 * scroll. All spatial checks are restricted to elements currently intersecting the viewport,
 * per the round-3 brief.
 */
function collectDeepChecks() {
  const innerW = window.innerWidth;
  const innerH = window.innerHeight;
  const docEl = document.documentElement;

  function shortClass(el: Element): string {
    const c = el.getAttribute("class") ?? "";
    return c.length > 80 ? c.slice(0, 80) + "…" : c;
  }
  function shortText(el: Element): string {
    const t = (el.textContent ?? "").trim().replace(/\s+/g, " ");
    return t.length > 60 ? t.slice(0, 60) + "…" : t;
  }
  function inViewport(r: DOMRect): boolean {
    return r.bottom > 0 && r.top < innerH && r.right > 0 && r.left < innerW && (r.width > 0 || r.height > 0);
  }

  const allEls = Array.from(document.querySelectorAll<HTMLElement>("*"));
  const viewportEls = allEls.filter((el) => inViewport(el.getBoundingClientRect()));

  // a. horizontal overflow (document-level)
  const hasOverflow = docEl.scrollWidth > innerW + 1;
  const overflowOffenders: unknown[] = [];
  if (hasOverflow) {
    for (const el of allEls) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      if (r.right > innerW + 1 || r.left < -1) {
        overflowOffenders.push({
          tag: el.tagName,
          class: shortClass(el),
          text: shortText(el),
          left: Math.round(r.left),
          right: Math.round(r.right),
        });
      }
      if (overflowOffenders.length >= 10) break;
    }
  }

  // b. clipped headings / cards / table cells (viewport-restricted)
  const HEADING_SEL = "h1,h2,h3,h4,[role=heading],th,td,.card-title";
  const clipped: unknown[] = [];
  for (const el of Array.from(document.querySelectorAll<HTMLElement>(HEADING_SEL))) {
    if (!inViewport(el.getBoundingClientRect())) continue;
    if (el.scrollWidth > el.clientWidth + 2) {
      const cs = getComputedStyle(el);
      if (["hidden", "clip"].includes(cs.overflowX) || cs.textOverflow === "ellipsis") {
        clipped.push({
          tag: el.tagName,
          class: shortClass(el),
          text: shortText(el),
          fullTextLength: (el.textContent ?? "").trim().length,
          scrollWidth: el.scrollWidth,
          clientWidth: el.clientWidth,
          overflowX: cs.overflowX,
          textOverflow: cs.textOverflow,
          whiteSpace: cs.whiteSpace,
        });
      }
    }
  }

  // c. tiny text (viewport-restricted, leaf-ish nodes only)
  let tinyCount = 0;
  const tinyExamples: unknown[] = [];
  const leafTextEls: HTMLElement[] = [];
  for (const el of viewportEls) {
    const t = (el.textContent ?? "").trim();
    if (!t || el.children.length > 0) continue;
    leafTextEls.push(el);
    const cs = getComputedStyle(el);
    const size = parseFloat(cs.fontSize);
    if (size > 0 && size < 12 && cs.visibility !== "hidden" && cs.display !== "none") {
      tinyCount++;
      if (tinyExamples.length < 10) {
        tinyExamples.push({ tag: el.tagName, class: shortClass(el), text: shortText(el), fontSize: size });
      }
    }
  }

  // d. tables (viewport-restricted)
  const tables: unknown[] = [];
  for (const table of Array.from(document.querySelectorAll<HTMLTableElement>("table"))) {
    if (!inViewport(table.getBoundingClientRect())) continue;
    const firstRow = table.querySelector("tr");
    const colCount = firstRow ? firstRow.children.length : 0;
    let container: HTMLElement | null = table.parentElement;
    let hasScrollWrapper = false;
    let hops = 0;
    while (container && hops < 4) {
      const cs = getComputedStyle(container);
      if (cs.overflowX === "auto" || cs.overflowX === "scroll") {
        hasScrollWrapper = true;
        break;
      }
      container = container.parentElement;
      hops++;
    }
    const containerClientWidth = table.parentElement?.clientWidth ?? innerW;
    tables.push({
      class: shortClass(table),
      colCount,
      tableScrollWidth: table.scrollWidth,
      containerClientWidth,
      overflows: table.scrollWidth > containerClientWidth + 2,
      hasScrollWrapper,
    });
  }

  // e. fixed/sticky elements covering >25% (kept, legacy) and >40% (new sweep threshold)
  const fixedCovering: unknown[] = [];
  const fixedCoveringOver40: unknown[] = [];
  for (const el of allEls) {
    const cs = getComputedStyle(el);
    if (cs.position === "fixed" || cs.position === "sticky") {
      const r = el.getBoundingClientRect();
      if (r.height > innerH * 0.25) {
        fixedCovering.push({
          tag: el.tagName,
          class: shortClass(el),
          position: cs.position,
          height: Math.round(r.height),
          pctOfViewport: Math.round((r.height / innerH) * 100),
        });
      }
      const area = Math.max(0, Math.min(r.right, innerW) - Math.max(r.left, 0)) *
        Math.max(0, Math.min(r.bottom, innerH) - Math.max(r.top, 0));
      if (area > 0.4 * innerW * innerH) {
        fixedCoveringOver40.push({
          tag: el.tagName,
          class: shortClass(el),
          position: cs.position,
          pctOfViewportArea: Math.round((area / (innerW * innerH)) * 100),
        });
      }
    }
  }

  // f. touch targets (viewport-restricted)
  let smallTargetCount = 0;
  const smallTargetExamples: unknown[] = [];
  for (const el of Array.from(document.querySelectorAll<HTMLElement>("a,button,[role=button],input,select"))) {
    const r = el.getBoundingClientRect();
    if (!inViewport(r)) continue;
    if (r.width === 0 && r.height === 0) continue;
    if (r.width < 40 || r.height < 40) {
      smallTargetCount++;
      if (smallTargetExamples.length < 10) {
        smallTargetExamples.push({
          tag: el.tagName,
          class: shortClass(el),
          text: shortText(el),
          width: Math.round(r.width),
          height: Math.round(r.height),
        });
      }
    }
  }

  // g. direction problems: computed direction ltr but contains Hebrew chars (viewport-restricted)
  const HEB_RE = /[\u0590-\u05FF]/;
  let ltrHebrewCount = 0;
  const ltrHebrewExamples: unknown[] = [];
  for (const el of viewportEls) {
    const own = Array.from(el.childNodes)
      .filter((n) => n.nodeType === Node.TEXT_NODE)
      .map((n) => n.textContent ?? "")
      .join("");
    if (!HEB_RE.test(own)) continue;
    const cs = getComputedStyle(el);
    if (cs.direction === "ltr") {
      ltrHebrewCount++;
      if (ltrHebrewExamples.length < 10) {
        ltrHebrewExamples.push({ tag: el.tagName, class: shortClass(el), text: shortText(el) });
      }
    }
  }

  // h. NEW: text-node overlap — two leaf text elements whose rects intersect by >30% of the
  // smaller rect's area, and neither contains the other.
  const overlapPairs: unknown[] = [];
  const candidates = leafTextEls.slice(0, 400); // cheap safety cap for pathological pages
  for (let i = 0; i < candidates.length && overlapPairs.length < 15; i++) {
    const a = candidates[i];
    const ra = a.getBoundingClientRect();
    if (ra.width < 2 || ra.height < 2) continue;
    for (let j = i + 1; j < candidates.length; j++) {
      const b = candidates[j];
      if (a === b || a.contains(b) || b.contains(a)) continue;
      const rb = b.getBoundingClientRect();
      if (rb.width < 2 || rb.height < 2) continue;
      const ix = Math.max(0, Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left));
      const iy = Math.max(0, Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top));
      const interArea = ix * iy;
      if (interArea <= 0) continue;
      const smaller = Math.min(ra.width * ra.height, rb.width * rb.height);
      if (smaller > 0 && interArea / smaller > 0.3) {
        overlapPairs.push({
          a: { tag: a.tagName, class: shortClass(a), text: shortText(a) },
          b: { tag: b.tagName, class: shortClass(b), text: shortText(b) },
          overlapPct: Math.round((interArea / smaller) * 100),
        });
        if (overlapPairs.length >= 15) break;
      }
    }
  }

  // i. NEW: element clipped by an ancestor with overflow hidden/clip — el's rect pokes outside
  // that ancestor's own rect on the clipped axis.
  const ancestorClipped: unknown[] = [];
  for (const el of leafTextEls) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    let node: HTMLElement | null = el.parentElement;
    let hops = 0;
    while (node && hops < 6) {
      const cs = getComputedStyle(node);
      const clipsX = cs.overflowX === "hidden" || cs.overflowX === "clip";
      const clipsY = cs.overflowY === "hidden" || cs.overflowY === "clip";
      if (clipsX || clipsY) {
        const ar = node.getBoundingClientRect();
        const pokesX = clipsX && (r.left < ar.left - 1 || r.right > ar.right + 1);
        const pokesY = clipsY && (r.top < ar.top - 1 || r.bottom > ar.bottom + 1);
        if ((pokesX || pokesY) && inViewport(r)) {
          ancestorClipped.push({
            tag: el.tagName,
            class: shortClass(el),
            text: shortText(el),
            ancestorClass: shortClass(node),
            elRect: { l: Math.round(r.left), t: Math.round(r.top), r: Math.round(r.right), b: Math.round(r.bottom) },
            ancestorRect: { l: Math.round(ar.left), t: Math.round(ar.top), r: Math.round(ar.right), b: Math.round(ar.bottom) },
          });
        }
        break; // nearest clipping ancestor only
      }
      node = node.parentElement;
      hops++;
    }
    if (ancestorClipped.length >= 15) break;
  }

  // j. NEW: images/canvases wider than the viewport
  const oversizeMedia: unknown[] = [];
  for (const el of Array.from(document.querySelectorAll<HTMLElement>("img,canvas,svg"))) {
    const r = el.getBoundingClientRect();
    if (!inViewport(r)) continue;
    if (r.width > innerW + 1) {
      oversizeMedia.push({ tag: el.tagName, class: shortClass(el), width: Math.round(r.width), innerW });
    }
  }

  // k. NEW: activeElement visible?
  const active = document.activeElement as HTMLElement | null;
  let activeElementInfo: unknown = null;
  if (active && active !== document.body) {
    const r = active.getBoundingClientRect();
    activeElementInfo = {
      tag: active.tagName,
      class: shortClass(active),
      visible: inViewport(r) && getComputedStyle(active).visibility !== "hidden",
      rect: { l: Math.round(r.left), t: Math.round(r.top), r: Math.round(r.right), b: Math.round(r.bottom) },
    };
  }

  // l. NEW: main horizontal overflow (inner scroll container, distinct from document-level (a))
  const main = document.querySelector("main");
  const mainOverflow = main ? main.scrollWidth > main.clientWidth + 1 : false;

  // m. NEW: phantom outer/document scroll — was a bug per the brief, must be false everywhere.
  const phantomOuterScroll = docEl.scrollHeight > innerH + 1;

  return {
    viewport: { width: innerW, height: innerH },
    title: document.title,
    h1: document.querySelector("h1")?.textContent?.trim() ?? null,
    scrollTop: main ? main.scrollTop : window.scrollY,
    horizontalOverflow: { present: hasOverflow, scrollWidth: docEl.scrollWidth, innerWidth: innerW, offenders: overflowOffenders },
    clippedHeadings: { count: clipped.length, examples: clipped },
    tinyText: { count: tinyCount, examples: tinyExamples },
    tables,
    fixedOrStickyCovering: fixedCovering,
    fixedOrStickyOver40pct: fixedCoveringOver40,
    smallTouchTargets: { count: smallTargetCount, examples: smallTargetExamples },
    ltrWithHebrew: { count: ltrHebrewCount, examples: ltrHebrewExamples },
    textOverlap: { count: overlapPairs.length, examples: overlapPairs },
    ancestorClipped: { count: ancestorClipped.length, examples: ancestorClipped },
    oversizeMedia,
    activeElementInfo,
    mainHorizontalOverflow: mainOverflow,
    phantomOuterScroll,
  };
}

async function shoot(page: Page, fileBase: string) {
  await page.screenshot({ path: path.join(OUT_DIR, `${fileBase}.png`) });
}

async function writeChecks(page: Page, fileBase: string) {
  const result = await page.evaluate(collectDeepChecks).catch((e) => ({ error: String(e) }));
  fs.writeFileSync(path.join(OUT_DIR, `${fileBase}-checks.json`), JSON.stringify(result, null, 2));
}

async function shootAndCheck(page: Page, fileBase: string) {
  await shoot(page, fileBase);
  await writeChecks(page, fileBase);
}

/** Section A: scroll `<main>` in steps of (clientHeight - 80px), capped at 12 steps. */
async function scrollThroughMain(page: Page, fileBasePrefix: string, maxSteps = 12) {
  let lastScrollTop = -1;
  for (let step = 0; step < maxSteps; step++) {
    const name = `${fileBasePrefix}-s${String(step).padStart(2, "0")}`;
    await shootAndCheck(page, name);
    const { scrollTop, stepPx, reachedEnd } = await page.evaluate(() => {
      const main = document.querySelector("main");
      if (!main) return { scrollTop: 0, stepPx: 0, reachedEnd: true };
      const stepPx = Math.max(100, main.clientHeight - 80);
      const before = main.scrollTop;
      main.scrollTop = before + stepPx;
      const after = main.scrollTop;
      const reachedEnd = after <= before + 1 || after + main.clientHeight >= main.scrollHeight - 1;
      return { scrollTop: after, stepPx, reachedEnd };
    });
    await page.waitForTimeout(220);
    if (reachedEnd || scrollTop === lastScrollTop) break;
    lastScrollTop = scrollTop;
  }
}

async function gotoWithTheme(page: Page, urlPath: string, theme: "light" | "dark", waitMs = 1200) {
  await page.goto(urlPath);
  await setTheme(page, theme);
  await page.reload();
  await page.waitForTimeout(waitMs);
}

async function safeClick(page: Page, locator: ReturnType<Page["locator"]>, timeout = 3000): Promise<boolean> {
  try {
    if (!(await locator.first().isVisible({ timeout }).catch(() => false))) return false;
    await locator.first().click({ timeout });
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------------------------
// SECTION A — scroll-through of every route
// ---------------------------------------------------------------------------------------------

interface RouteScreen {
  name: string;
  path: string;
  waitMs?: number;
  maxSteps?: number;
  /** Extra step run once after navigation+theme, before scrolling (e.g. click a tab). */
  preScroll?: (page: Page) => Promise<void>;
}

const ROUTES_A: RouteScreen[] = [
  { name: "a01-morning", path: "/" },
  { name: "a02-feed", path: "/feed" },
  { name: "a03-item-detail", path: "/items/19384" },
  { name: "a04-entities-list", path: "/entities" },
  { name: "a05-entity-detail", path: "/entities/14" },
  { name: "a06-entities-graph", path: "/entities?view=graph" },
  { name: "a07-investigations-list", path: "/investigations" },
  { name: "a08-investigation-detail", path: "/investigations/288" },
  { name: "a09-ask", path: "/ask" },
  { name: "a10-conferences", path: "/conferences" },
  { name: "a11-tenders", path: "/tenders" },
  { name: "a11b-tenders-forecast", path: "/tenders", preScroll: (p) => clickTabByText(p, "תחזית מכרזים") },
  { name: "a12-patents", path: "/patents" },
  { name: "a12b-patents-cpc-matrix", path: "/patents", preScroll: (p) => clickTabByText(p, "מטריצת CPC") },
  { name: "a12c-patents-survey", path: "/patents", preScroll: (p) => clickTabByText(p, "סקר פטנטים") },
  { name: "a13-payloads", path: "/payloads" },
  { name: "a14-inbox", path: "/inbox" },
  { name: "a15-reports-list", path: "/reports" },
  { name: "a16-reports-monthly", path: "/reports?id=191", waitMs: 1200, maxSteps: 12 },
  { name: "a18-bd", path: "/bd" },
  { name: "a19-product-lines", path: "/product-lines" },
  { name: "a20-product-line-detail", path: "/product-lines/targeting_pods" },
  { name: "a21-dossiers-list", path: "/dossiers" },
  { name: "a22-dossier-detail", path: "/dossiers/elbit-systems-spectro-xr", waitMs: 1600, maxSteps: 12 },
  {
    name: "a23-dossier-compare",
    path: "/dossiers/compare?keys=elbit-systems-spectro-xr,lockheed-martin-sniper-advanced-targeting-pod,rafael-advanced-defense-systems-litening-5",
    waitMs: 1600,
  },
  { name: "a24-tech-radar", path: "/tech-radar" },
  { name: "a25-settings", path: "/settings" },
];

async function clickTabByText(page: Page, text: string) {
  await safeClick(page, page.getByRole("tab", { name: new RegExp(text) }));
  await page.waitForTimeout(400);
}

test.describe("A — scroll-through (light)", () => {
  for (const screen of ROUTES_A) {
    test(`${screen.name} — light`, async ({ page }) => {
      await gotoWithTheme(page, screen.path, "light", screen.waitMs);
      if (screen.preScroll) await screen.preScroll(page);
      await scrollThroughMain(page, `${screen.name}_light`, screen.maxSteps ?? 12);
    });
  }
});

const DARK_SUBSET: RouteScreen[] = [
  { name: "a01-morning", path: "/" },
  { name: "a02-feed", path: "/feed" },
  { name: "a22-dossier-detail", path: "/dossiers/elbit-systems-spectro-xr", waitMs: 1600 },
  { name: "a16-reports-monthly", path: "/reports?id=191", waitMs: 1200 },
];

test.describe("A — dark theme subset", () => {
  for (const screen of DARK_SUBSET) {
    test(`${screen.name} — dark`, async ({ page }) => {
      await gotoWithTheme(page, screen.path, "dark", screen.waitMs);
      await scrollThroughMain(page, `${screen.name}_dark`, 8);
    });
  }
});

const LANDSCAPE_SUBSET: RouteScreen[] = [
  { name: "a01-morning", path: "/" },
  { name: "a02-feed", path: "/feed" },
  { name: "a16-reports-monthly", path: "/reports?id=191", waitMs: 1200 },
  { name: "a22-dossier-detail", path: "/dossiers/elbit-systems-spectro-xr", waitMs: 1600 },
];

test.describe("A — landscape subset (844x390)", () => {
  for (const screen of LANDSCAPE_SUBSET) {
    test(`${screen.name} — landscape`, async ({ page }) => {
      await page.setViewportSize({ width: 844, height: 390 });
      await gotoWithTheme(page, screen.path, "light", screen.waitMs);
      await scrollThroughMain(page, `${screen.name}_landscape`, 8);
    });
  }
});

// ---------------------------------------------------------------------------------------------
// SECTION B — interactive states
// ---------------------------------------------------------------------------------------------

test.describe("B — interactive states", () => {
  test("b01-bottom-nav-more-sheet", async ({ page }) => {
    await gotoWithTheme(page, "/feed", "light");
    await shootAndCheck(page, "b01a-before");
    const opened = await safeClick(page, page.getByTestId("mobile-nav-more"));
    await page.waitForTimeout(300);
    await shootAndCheck(page, `b01b-sheet-open_${opened}`);
    // Escape should close it.
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    await shootAndCheck(page, "b01c-after-escape");
  });

  test("b02-topbar-overflow-menu", async ({ page }) => {
    await gotoWithTheme(page, "/feed", "light");
    const opened = await safeClick(page, page.getByTestId("topbar-overflow-toggle"));
    await page.waitForTimeout(300);
    await shootAndCheck(page, `b02a-overflow-open_${opened}`);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(200);
    await shootAndCheck(page, "b02b-overflow-closed");
  });

  test("b03-topbar-search", async ({ page }) => {
    await gotoWithTheme(page, "/feed", "light");
    const opened = await safeClick(page, page.getByLabel("חיפוש גלובלי"));
    await page.waitForTimeout(300);
    await shootAndCheck(page, `b03a-search-open_${opened}`);
    if (opened) {
      try {
        await page.keyboard.type("אלביט", { delay: 30 });
        await page.waitForTimeout(900);
      } catch {
        /* best-effort */
      }
    }
    await shootAndCheck(page, "b03b-search-results");
    await page.keyboard.press("Escape");
  });

  test("b04-runnow-popover", async ({ page }) => {
    await gotoWithTheme(page, "/", "light");
    // Only open if a popover-producing state is reachable without mutating: if idle, clicking
    // triggers a REAL run — the brief only asks to screenshot "popover/confirm if any", so only
    // click when the button is already busy (shows the progress popover harmlessly); otherwise
    // just screenshot the idle button.
    const busy = await page.getByRole("button", { name: /פעיל|running|Loader/i }).count().catch(() => 0);
    await shootAndCheck(page, "b04a-runnow-idle-or-busy-button");
    if (busy > 0) {
      await safeClick(page, page.locator('[aria-haspopup="true"]').first());
      await page.waitForTimeout(300);
      await shootAndCheck(page, "b04b-runnow-popover-open");
    }
  });

  test("b05-status-strip-drawer", async ({ page }) => {
    await gotoWithTheme(page, "/", "light");
    await shootAndCheck(page, "b05a-status-strip-collapsed");
    const opened = await safeClick(page, page.locator('footer[role="status"] button').first());
    await page.waitForTimeout(400);
    await shootAndCheck(page, `b05b-status-strip-drawer_${opened}`);
  });

  test("b06-chat-panel-real-question", async ({ page }) => {
    test.setTimeout(150_000);
    await gotoWithTheme(page, "/", "light");
    const opened = await safeClick(page, page.getByLabel("פתח את פאנל שאל את האנליסט", { exact: false }));
    await page.waitForTimeout(400);
    await shootAndCheck(page, `b06a-chat-panel-open_${opened}`);

    const input = page.getByPlaceholder("שאל שאלה…");
    const inputVisible = await input.isVisible({ timeout: 3000 }).catch(() => false);
    if (inputVisible) {
      // Measure input rect vs viewport (keyboard-safe-area / bottom-bar overlap check).
      const rect = await input.boundingBox();
      fs.writeFileSync(
        path.join(OUT_DIR, "b06-chat-input-rect.json"),
        JSON.stringify({ rect, viewport: page.viewportSize() }, null, 2),
      );
      await input.fill("מה חדש באלביט השבוע?");
      await shootAndCheck(page, "b06b-chat-input-filled");
      await input.press("Enter");
      await page.waitForTimeout(1500);
      await shootAndCheck(page, "b06c-chat-streaming");
      // Wait up to ~90s for a final answer (no more "streaming" state / stop button gone).
      await page
        .waitForFunction(
          () => !document.querySelector('button[aria-label="עצור"]'),
          undefined,
          { timeout: 90_000 },
        )
        .catch(() => {});
      await page.waitForTimeout(500);
      await shootAndCheck(page, "b06d-chat-final-answer");
      // Model picker.
      const pickerOpened = await safeClick(page, page.locator('[data-testid*="model-picker"], select').first());
      await page.waitForTimeout(300);
      await shootAndCheck(page, `b06e-model-picker_${pickerOpened}`);
    }
  });

  test("b07-feed-filters", async ({ page }) => {
    await gotoWithTheme(page, "/feed", "light");
    await shootAndCheck(page, "b07a-feed-base");

    const countryOpened = await safeClick(page, page.getByTestId("country-filter-toggle"));
    await page.waitForTimeout(300);
    await shootAndCheck(page, `b07b-country-menu_${countryOpened}`);
    if (countryOpened) await safeClick(page, page.getByTestId("country-filter-toggle"));

    const plOpened = await safeClick(page, page.getByTestId("product-line-filter-toggle"));
    await page.waitForTimeout(300);
    await shootAndCheck(page, `b07c-productline-menu_${plOpened}`);
    if (plOpened) await safeClick(page, page.getByTestId("product-line-filter-toggle"));

    // Explain-score / corroboration popovers: open on the first row that has them. The trigger
    // button's aria-label is "למה הציון?" (ExplainScorePopover.tsx:79); the popover panel itself
    // carries aria-label="הסבר ציון" but is not the clickable element.
    const explainOpened = await safeClick(page, page.getByLabel("למה הציון?").first());
    await page.waitForTimeout(300);
    await shootAndCheck(page, `b07d-explain-score-popover_${explainOpened}`);
    await page.keyboard.press("Escape").catch(() => {});

    const corrOpened = await safeClick(page, page.locator('[data-testid^="corroboration-badge-"]').first());
    await page.waitForTimeout(300);
    await shootAndCheck(page, `b07e-corroboration-popover_${corrOpened}`);
    await page.keyboard.press("Escape").catch(() => {});

    const singleSourceToggled = await safeClick(page, page.getByTestId("single-source-filter-toggle"));
    await page.waitForTimeout(400);
    await shootAndCheck(page, `b07f-single-source-toggled_${singleSourceToggled}`);

    // Tap a row: drawer or detail?
    const row = page.locator('[data-testid^="feed-row-"]').first();
    const rowClicked = await safeClick(page, row);
    await page.waitForTimeout(500);
    await shootAndCheck(page, `b07g-row-tap-result_${rowClicked}`);
  });

  test("b08-item-detail-actions", async ({ page }) => {
    await gotoWithTheme(page, "/items/19384", "light", 1200);
    await shootAndCheck(page, "b08a-item-detail-base");

    const addedToContext = await safeClick(page, page.getByText("הוסף להקשר", { exact: false }).first());
    await page.waitForTimeout(400);
    await shootAndCheck(page, `b08b-add-to-context_${addedToContext}`);
    // Close the chat panel it opens, to not interfere with later steps.
    await safeClick(page, page.getByLabel("סגור", { exact: false }).first());

    // IMPORTANT: `ItemDetailPage`'s "חקור לעומק" button calls `investigate.mutate()` directly on
    // click (web/src/pages/ItemDetailPage.tsx:173-179) -- there is no confirmation dialog to
    // screenshot-and-cancel; clicking it immediately queues a REAL investigation job against the
    // live backend. Per the hard rule against real side effects, screenshot the button in place
    // only -- never click it. (The absence of a confirm step here is itself worth flagging in the
    // report, not exercising it.)
    const investigateBtn = page.getByText("חקור לעומק", { exact: false }).first();
    if (await investigateBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await investigateBtn.scrollIntoViewIfNeeded().catch(() => {});
      await shootAndCheck(page, "b08c-investigate-button-in-place-not-clicked");
    }

    const recheckClicked = await safeClick(page, page.getByTestId("corroboration-recheck-button"));
    await page.waitForTimeout(1500);
    await shootAndCheck(page, `b08e-recheck-verification_${recheckClicked}`);

    // Source preview card + appears-in-reports chips: scroll to find them, screenshot in place.
    const sourceCard = page.getByTestId("source-preview-card").first();
    if (await sourceCard.isVisible({ timeout: 2000 }).catch(() => false)) {
      await sourceCard.scrollIntoViewIfNeeded().catch(() => {});
      await page.waitForTimeout(300);
      await shootAndCheck(page, "b08f-source-preview-card");
    }
    const reportsChips = page.getByText("מופיע בדוחות", { exact: false }).first();
    if (await reportsChips.isVisible({ timeout: 2000 }).catch(() => false)) {
      await reportsChips.scrollIntoViewIfNeeded().catch(() => {});
      await page.waitForTimeout(300);
      await shootAndCheck(page, "b08g-appears-in-reports-chips");
    }
  });

  test("b09-dossiers-new-form-and-rerun", async ({ page }) => {
    await gotoWithTheme(page, "/dossiers", "light");
    await shootAndCheck(page, "b09a-dossiers-list");

    const formOpened = await safeClick(page, page.getByTestId("dossier-new-button"));
    await page.waitForTimeout(400);
    await shootAndCheck(page, `b09b-new-dossier-form_${formOpened}`);
    if (formOpened) {
      const nameInput = page.locator("#dossier-product-name");
      if (await nameInput.isVisible({ timeout: 2000 }).catch(() => false)) {
        await nameInput.fill("בדיקת קריאות — לא לשלוח");
        await page.waitForTimeout(200);
        await shootAndCheck(page, "b09c-new-dossier-form-filled");
      }
      // Cancel, never submit — this would queue a real research job.
      await safeClick(page, page.getByRole("button", { name: /ביטול/i }));
      await page.waitForTimeout(300);
      await shootAndCheck(page, "b09d-new-dossier-form-cancelled");
    }

    // Rerun button: screenshot only — no confirmation step exists in the code (direct
    // mutation on click), so clicking it would queue a real re-investigation. Documented as a
    // finding, not exercised.
    const rerunBtn = page.locator('[data-testid^="dossier-rerun-"]').first();
    if (await rerunBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await rerunBtn.scrollIntoViewIfNeeded().catch(() => {});
      await page.waitForTimeout(200);
      await shootAndCheck(page, "b09e-rerun-button-in-place");
    }
  });

  test("b10-dossier-detail-section-nav-and-rerun", async ({ page }) => {
    await gotoWithTheme(page, "/dossiers/elbit-systems-spectro-xr", "light", 1600);
    await shootAndCheck(page, "b10a-dossier-detail-top");

    // Same no-confirm caveat as b09e — screenshot only, never click.
    const rerunBtn = page.getByTestId("dossier-detail-rerun");
    if (await rerunBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await rerunBtn.scrollIntoViewIfNeeded().catch(() => {});
      await shootAndCheck(page, "b10b-rerun-button-in-place");
    }

    // Section nav chip tap -> verify the section heading lands below the sticky bars.
    const chip = page.locator('nav[aria-label="ניווט בין סעיפי הסקירה"] a[href="#dossier-deals"]');
    const chipClicked = await safeClick(page, chip);
    await page.waitForTimeout(500);
    await shootAndCheck(page, `b10c-section-nav-jump-deals_${chipClicked}`);
    if (chipClicked) {
      const rects = await page.evaluate(() => {
        const heading = document.getElementById("dossier-deals");
        const nav = document.querySelector('nav[aria-label="ניווט בין סעיפי הסקירה"]');
        const topbar = document.querySelector("header");
        const hr = heading?.getBoundingClientRect();
        const nr = nav?.getBoundingClientRect();
        const tr = topbar?.getBoundingClientRect();
        return { heading: hr ? { top: hr.top, bottom: hr.bottom } : null, nav: nr ? { bottom: nr.bottom } : null, topbar: tr ? { bottom: tr.bottom } : null };
      });
      fs.writeFileSync(path.join(OUT_DIR, "b10c-section-nav-rects.json"), JSON.stringify(rects, null, 2));
    }
  });

  test("b11-dossier-compare-selection", async ({ page }) => {
    await gotoWithTheme(page, "/dossiers", "light");
    // Select two compare checkboxes if present.
    const checkboxes = page.locator('[data-testid^="dossier-compare-checkbox-"]');
    const count = await checkboxes.count().catch(() => 0);
    for (let i = 0; i < Math.min(2, count); i++) {
      await safeClick(page, checkboxes.nth(i));
      await page.waitForTimeout(150);
    }
    await shootAndCheck(page, `b11a-compare-selection_${count}`);
    const compareBtn = page.getByTestId("dossier-compare-selected-button");
    if (await compareBtn.isVisible({ timeout: 1500 }).catch(() => false)) {
      await compareBtn.scrollIntoViewIfNeeded().catch(() => {});
      await shootAndCheck(page, "b11b-compare-button-reachable");
    }
  });

  test("b12-reports-controls", async ({ page }) => {
    await gotoWithTheme(page, "/reports?id=191", "light", 1200);
    await shootAndCheck(page, "b12a-report-top");

    const backLink = page.getByText("חזרה לרשימה", { exact: false }).first();
    const backVisible = await backLink.isVisible({ timeout: 2000 }).catch(() => false);
    await shootAndCheck(page, `b12b-back-to-list-reachable_${backVisible}`);

    await gotoWithTheme(page, "/reports", "light");
    const typeSelect = page.locator("select").first();
    if (await typeSelect.isVisible({ timeout: 2000 }).catch(() => false)) {
      await typeSelect.scrollIntoViewIfNeeded().catch(() => {});
      await shootAndCheck(page, "b12c-type-filter-select");
    }
    const olderExpander = page.getByText("גרסאות קודמות", { exact: false }).first();
    const expanderOpened = await safeClick(page, olderExpander);
    await page.waitForTimeout(300);
    await shootAndCheck(page, `b12d-older-versions-expander_${expanderOpened}`);
  });

  test("b13-tenders", async ({ page }) => {
    await gotoWithTheme(page, "/tenders", "light");
    await shootAndCheck(page, "b13a-tenders-base");
    await clickTabByText(page, "תחזית מכרזים");
    await shootAndCheck(page, "b13b-tenders-forecast-tab");
    await gotoWithTheme(page, "/tenders", "light");
    // Target the card's own chevron-expand toggle specifically (lucide's ChevronDown renders
    // with a `lucide-chevron-down` class) -- NOT a blind "first button with an svg", which would
    // just as likely hit TopBar's search/run-now icon buttons before ever reaching a card.
    const expanded = await safeClick(page, page.locator('main button:has(svg.lucide-chevron-down)').first());
    await page.waitForTimeout(300);
    await shootAndCheck(page, `b13c-card-expand_${expanded}`);
  });

  test("b14-patents-tabs", async ({ page }) => {
    await gotoWithTheme(page, "/patents", "light");
    await shootAndCheck(page, "b14a-patents-base");
    await clickTabByText(page, "מטריצת CPC");
    await shootAndCheck(page, "b14b-cpc-matrix-tab");
    await gotoWithTheme(page, "/patents", "light");
    await clickTabByText(page, "סקר פטנטים");
    await shootAndCheck(page, "b14c-survey-tab");
  });

  test("b15-entities-list-detail-graph", async ({ page }) => {
    await gotoWithTheme(page, "/entities", "light");
    await shootAndCheck(page, "b15a-entities-list");
    await gotoWithTheme(page, "/entities/14", "light");
    await shootAndCheck(page, "b15b-entity-detail");
    const graphLink = page.getByText("פתח גרף מלא", { exact: false }).first();
    const graphClicked = await safeClick(page, graphLink);
    await page.waitForTimeout(600);
    await shootAndCheck(page, `b15c-open-full-graph_${graphClicked}`);
  });

  test("b16-tech-radar-payloads-inbox-bd-productline-conferences", async ({ page }) => {
    await gotoWithTheme(page, "/tech-radar", "light");
    await shootAndCheck(page, "b16a-tech-radar-matrix");

    await gotoWithTheme(page, "/payloads", "light");
    await shootAndCheck(page, "b16b-payloads-table");

    await gotoWithTheme(page, "/inbox", "light");
    await shootAndCheck(page, "b16c-inbox-base");
    // Every button on this page mutates real state (approve/dismiss a security review, delete a
    // lesson, submit a clarification answer) with no confirm step -- per the hard rule against
    // real side effects, document reachability/tap-target size only, never click.
    const actionBtn = page.locator("main button").first();
    if (await actionBtn.isVisible({ timeout: 1500 }).catch(() => false)) {
      await actionBtn.scrollIntoViewIfNeeded().catch(() => {});
      await shootAndCheck(page, "b16d-inbox-action-button-in-place");
    }

    await gotoWithTheme(page, "/bd", "light");
    await shootAndCheck(page, "b16e-bd-tables");

    await gotoWithTheme(page, "/product-lines/targeting_pods", "light");
    await shootAndCheck(page, "b16f-product-line-detail-base");
    const tab = page.getByRole("tab").nth(1);
    const tabClicked = await safeClick(page, tab);
    await page.waitForTimeout(300);
    await shootAndCheck(page, `b16g-product-line-detail-tab2_${tabClicked}`);

    await gotoWithTheme(page, "/conferences", "light");
    await shootAndCheck(page, "b16h-conferences");
  });

  test("b17-settings", async ({ page }) => {
    await gotoWithTheme(page, "/settings", "light");
    await shootAndCheck(page, "b17a-settings-models-section");

    const quickControls = page.getByText("בקרות מהירות", { exact: false }).first();
    if (await quickControls.isVisible({ timeout: 2000 }).catch(() => false)) {
      await quickControls.scrollIntoViewIfNeeded().catch(() => {});
      await shootAndCheck(page, "b17b-settings-quick-controls");
    }

    const yamlSection = page.getByText("עריכת קבצי הגדרה", { exact: false }).first();
    if (await yamlSection.isVisible({ timeout: 2000 }).catch(() => false)) {
      await yamlSection.scrollIntoViewIfNeeded().catch(() => {});
      await shootAndCheck(page, "b17c-settings-yaml-tabs");
      const tabs = page.locator('[role="tablist"] [role="tab"]');
      const tabCount = await tabs.count().catch(() => 0);
      for (let i = 0; i < tabCount; i++) {
        const clicked = await safeClick(page, tabs.nth(i));
        await page.waitForTimeout(300);
        await shootAndCheck(page, `b17d-settings-yaml-tab-${i}_${clicked}`);
      }
    }

    const jobsSection = page.getByText("עבודות", { exact: false }).first();
    if (await jobsSection.isVisible({ timeout: 2000 }).catch(() => false)) {
      await jobsSection.scrollIntoViewIfNeeded().catch(() => {});
      await shootAndCheck(page, "b17e-settings-jobs-table");
    }

    const saveBtn = page.getByRole("button", { name: /שמור/ });
    if (await saveBtn.first().isVisible({ timeout: 2000 }).catch(() => false)) {
      await saveBtn.first().scrollIntoViewIfNeeded().catch(() => {});
      await shootAndCheck(page, "b17f-settings-save-button-reachable");
    }
  });
});
