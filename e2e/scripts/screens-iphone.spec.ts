import { test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * iPhone/mobile-web readability audit pass (2026-09-15). Modelled directly on
 * `screens-tablet.spec.ts` -- same "not part of the pass/fail QA suite" reasoning applies (lives
 * outside e2e/tests on purpose). This pass answers the user's complaint that headings and core
 * collected information are unreadable on an iPhone (390x844-ish) over Tailscale. Captures a
 * viewport screenshot AND a full-page screenshot per screen, plus a `page.evaluate` readability
 * check (overflow, clipped headings, tiny text, tables, fixed/sticky coverage, touch targets,
 * ltr-with-Hebrew) dumped to a sibling `<route>-checks.json`.
 *
 * Run with (webkit = what the user actually sees; chromium-mobile = cross-check):
 *
 *   npx playwright test --config=scripts/pw.iphone-shots.config.ts \
 *     --project=iphone-safari --project=mobile-390x844
 *
 * Screenshots + checks JSON are written OUTSIDE the repo, to the audit scratchpad -- see
 * docs/qa/content_review/UI-MOBILE-iphone.md for the report built from this pass's output.
 */

const OUT_DIR =
  process.env.IPHONE_AUDIT_OUT_DIR ??
  "C:\\Users\\cohyo\\AppData\\Local\\Temp\\claude\\C--Users-cohyo-Documents-EO-analyst\\3b24a26d-4bdd-49a3-b2dd-f67f85df545c\\scratchpad\\iphone-audit";

async function setTheme(page: Page, theme: "dark" | "light") {
  // Same zustand persist shape as screens-tablet.spec.ts's setTheme -- a plain
  // document.documentElement.dataset.theme write alone gets clobbered by AppShell re-applying
  // the store's own theme on mount/reload.
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

// Runs entirely inside the page -- must be self-contained (no closures over outer-scope values
// besides its own argument), see page.evaluate semantics.
function collectChecks() {
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

  const allEls = Array.from(document.querySelectorAll<HTMLElement>("*"));

  // a. horizontal overflow
  const hasOverflow = docEl.scrollWidth > innerW;
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

  // b. clipped headings / cards / table cells
  const HEADING_SEL = "h1,h2,h3,h4,[role=heading],th,td,.card-title";
  const clipped: unknown[] = [];
  for (const el of Array.from(document.querySelectorAll<HTMLElement>(HEADING_SEL))) {
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

  // c. tiny text
  let tinyCount = 0;
  const tinyExamples: unknown[] = [];
  for (const el of allEls) {
    const t = (el.textContent ?? "").trim();
    if (!t || el.children.length > 0) continue; // leaf-ish nodes only, avoid double count
    const cs = getComputedStyle(el);
    const size = parseFloat(cs.fontSize);
    if (size > 0 && size < 12 && cs.visibility !== "hidden" && cs.display !== "none") {
      tinyCount++;
      if (tinyExamples.length < 10) {
        tinyExamples.push({ tag: el.tagName, class: shortClass(el), text: shortText(el), fontSize: size });
      }
    }
  }

  // d. tables
  const tables: unknown[] = [];
  for (const table of Array.from(document.querySelectorAll<HTMLTableElement>("table"))) {
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

  // e. fixed/sticky elements covering >25% of viewport height
  const fixedCovering: unknown[] = [];
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
    }
  }

  // f. touch targets
  let smallTargetCount = 0;
  const smallTargetExamples: unknown[] = [];
  for (const el of Array.from(document.querySelectorAll<HTMLElement>("a,button,[role=button],input,select"))) {
    const r = el.getBoundingClientRect();
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

  // g. direction problems: computed direction ltr but contains Hebrew chars
  const HEB_RE = /[\u0590-\u05FF]/;
  let ltrHebrewCount = 0;
  const ltrHebrewExamples: unknown[] = [];
  for (const el of allEls) {
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

  return {
    viewport: { width: innerW, height: innerH },
    title: document.title,
    h1: document.querySelector("h1")?.textContent?.trim() ?? null,
    horizontalOverflow: { present: hasOverflow, scrollWidth: docEl.scrollWidth, innerWidth: innerW, offenders: overflowOffenders },
    clippedHeadings: { count: clipped.length, examples: clipped },
    tinyText: { count: tinyCount, examples: tinyExamples },
    tables,
    fixedOrStickyCovering: fixedCovering,
    smallTouchTargets: { count: smallTargetCount, examples: smallTargetExamples },
    ltrWithHebrew: { count: ltrHebrewCount, examples: ltrHebrewExamples },
  };
}

async function writeChecks(page: Page, fileBase: string) {
  const result = await page.evaluate(collectChecks);
  fs.writeFileSync(path.join(OUT_DIR, `${fileBase}-checks.json`), JSON.stringify(result, null, 2));
}

async function shoot(page: Page, fileBase: string) {
  await page.screenshot({ path: path.join(OUT_DIR, `${fileBase}.png`), fullPage: false });
  await page.screenshot({ path: path.join(OUT_DIR, `${fileBase}-full.png`), fullPage: true });
}

interface Screen {
  name: string;
  path: string;
  waitMs?: number;
  /** Extra steps after load + theme + reload, before the base screenshot/checks pair. */
  action?: (page: Page) => Promise<void>;
  /** Additional named sub-shots taken after the base pair (e.g. scrolled-to-section). */
  extraShots?: { suffix: string; action: (page: Page) => Promise<void> }[];
  /** Skip the (expensive) ask-the-analyst round trip on this pass; still screenshots the page. */
  skipAsk?: boolean;
}

async function scrollToId(page: Page, id: string) {
  await page.evaluate((elId) => {
    document.getElementById(elId)?.scrollIntoView({ block: "start" });
  }, id);
  await page.waitForTimeout(300);
}

const SCREENS: Screen[] = [
  { name: "01-morning", path: "/" },
  { name: "02-feed", path: "/feed" },
  { name: "03-item-detail", path: "/items/19384" },
  { name: "04-entities-list", path: "/entities" },
  { name: "05-entity-detail", path: "/entities/14" },
  { name: "06-entities-graph", path: "/entities?view=graph" },
  { name: "07-investigations-list", path: "/investigations" },
  { name: "08-investigation-detail", path: "/investigations/288" },
  {
    name: "09-ask",
    path: "/ask",
  },
  {
    name: "10-conferences",
    path: "/conferences",
  },
  { name: "11-tenders", path: "/tenders" },
  { name: "12-patents", path: "/patents" },
  { name: "13-payloads", path: "/payloads" },
  { name: "14-inbox", path: "/inbox" },
  {
    name: "15-reports-list",
    path: "/reports",
  },
  {
    name: "16-reports-monthly",
    path: "/reports?id=191",
    waitMs: 800,
    extraShots: [
      {
        suffix: "table",
        action: async (page) => {
          const table = page.locator("table").first();
          if (await table.isVisible().catch(() => false)) {
            await table.scrollIntoViewIfNeeded().catch(() => {});
            await page.waitForTimeout(300);
          }
        },
      },
      {
        suffix: "heading",
        action: async (page) => {
          const heading = page.locator("h1,h2,h3").nth(1);
          if (await heading.isVisible().catch(() => false)) {
            await heading.scrollIntoViewIfNeeded().catch(() => {});
            await page.waitForTimeout(300);
          }
        },
      },
    ],
  },
  {
    name: "17-reports-weekly",
    path: "/reports?id=207",
    waitMs: 800,
    extraShots: [
      {
        suffix: "table",
        action: async (page) => {
          const table = page.locator("table").first();
          if (await table.isVisible().catch(() => false)) {
            await table.scrollIntoViewIfNeeded().catch(() => {});
            await page.waitForTimeout(300);
          }
        },
      },
    ],
  },
  { name: "18-bd", path: "/bd" },
  { name: "19-product-lines", path: "/product-lines" },
  { name: "20-product-line-detail", path: "/product-lines/targeting_pods" },
  { name: "21-dossiers-list", path: "/dossiers" },
  {
    name: "22-dossier-detail",
    path: "/dossiers/elbit-systems-spectro-xr",
    waitMs: 1200,
    extraShots: [
      { suffix: "spec", action: (p) => scrollToId(p, "dossier-specifications") },
      { suffix: "timeline", action: (p) => scrollToId(p, "dossier-timeline") },
      { suffix: "deals", action: (p) => scrollToId(p, "dossier-deals") },
      { suffix: "sources", action: (p) => scrollToId(p, "dossier-sources") },
    ],
  },
  {
    name: "23-dossier-compare",
    path: "/dossiers/compare?keys=elbit-systems-spectro-xr,lockheed-martin-sniper-advanced-targeting-pod,rafael-advanced-defense-systems-litening-5",
    waitMs: 1200,
  },
  { name: "24-tech-radar", path: "/tech-radar" },
  { name: "25-settings", path: "/settings" },
];

test.describe("iPhone screenshot + readability audit", () => {
  for (const theme of ["light", "dark"] as const) {
    for (const screen of SCREENS) {
      test(`${screen.name} — ${theme}`, async ({ page }, testInfo) => {
        fs.mkdirSync(OUT_DIR, { recursive: true });
        await page.goto(screen.path);
        await setTheme(page, theme);
        await page.reload();
        await page.waitForTimeout(1200 + (screen.waitMs ?? 0));

        // The Ask-the-analyst round trip is expensive (up to 60s) -- only exercise it once
        // per engine (light theme pass) to keep total runtime sane; dark theme still captures
        // the page's empty/input state, which is enough to judge input/label readability.
        if (screen.name === "09-ask" && theme === "light") {
          const input = page.getByPlaceholder("שאל שאלה…");
          if (await input.isVisible().catch(() => false)) {
            await input.fill("מה חדש באלביט?");
            await input.press("Enter");
            await page.waitForTimeout(60_000).catch(() => {});
          }
        }

        const base = `${screen.name}_${testInfo.project.name}_${theme}`;
        await shoot(page, base);
        await writeChecks(page, base);

        if (screen.extraShots) {
          for (const extra of screen.extraShots) {
            await extra.action(page);
            await shoot(page, `${base}_${extra.suffix}`);
          }
        }

        if (screen.action) {
          await screen.action(page);
        }
      });
    }
  }
});
