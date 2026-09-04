import { type Page, type TestInfo, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

export const BAD_TEXT_VALUES = ["undefined", "null", "NaN", "[object Object]"];

/**
 * Findings are appended as JSON-lines to report/findings.jsonl by every test
 * (via recordFinding) and by the console/page-error watchers registered in
 * attachConsoleErrorCollector. playwright.config.ts's globalTeardown reads
 * this file once, after the whole run, and renders QA_FINDINGS.md — do not
 * write QA_FINDINGS.md from inside a test, tests only run in this file's
 * own worker process and there is no shared in-memory state across files.
 */
const FINDINGS_FILE = path.join(__dirname, "..", "report", "findings.jsonl");

export interface Finding {
  screen: string;
  expected: string;
  actual: string;
  severity: "critical" | "high" | "medium" | "low";
  screenshotPath?: string;
  project: string;
  testTitle: string;
  at: string;
}

export function recordFindingSync(f: Omit<Finding, "at">) {
  fs.mkdirSync(path.dirname(FINDINGS_FILE), { recursive: true });
  const line = JSON.stringify({ ...f, at: new Date().toISOString() });
  fs.appendFileSync(FINDINGS_FILE, line + "\n", "utf-8");
}

/**
 * Takes a screenshot for a finding and records it. Best-effort: screenshot
 * failure never throws, a missing screenshot just means screenshotPath is
 * undefined in the report.
 */
export async function recordFinding(
  page: Page,
  testInfo: TestInfo,
  f: {
    screen: string;
    expected: string;
    actual: string;
    severity: Finding["severity"];
  },
) {
  let screenshotPath: string | undefined;
  try {
    const dir = path.join(__dirname, "..", "report", "finding-screenshots");
    fs.mkdirSync(dir, { recursive: true });
    const safeName = `${f.screen}-${testInfo.title}-${Date.now()}`
      .replace(/[^a-zA-Z0-9_-]+/g, "_")
      .slice(0, 150);
    screenshotPath = path.join(dir, `${safeName}.png`);
    await page.screenshot({ path: screenshotPath, fullPage: false });
  } catch {
    screenshotPath = undefined;
  }
  recordFindingSync({
    screen: f.screen,
    expected: f.expected,
    actual: f.actual,
    severity: f.severity,
    screenshotPath,
    project: testInfo.project.name,
    testTitle: testInfo.title,
  });
}

/**
 * Attaches console + pageerror listeners that collect uncaught JS errors.
 * Call at the top of a test (or in a fixture) BEFORE navigating. Returns an
 * accessor for the collected messages; call assertNoConsoleErrors() (or
 * inspect getErrors() yourself) at the point you want to fail on any.
 *
 * Ignores a small, explicit denylist of known-benign browser noise (e.g.
 * favicon 404s under jsdom-less real Chromium) — everything else uncaught
 * counts, per spec ("fail on any uncaught error").
 */
export function attachConsoleErrorCollector(page: Page) {
  const errors: string[] = [];

  page.on("console", (msg) => {
    if (msg.type() === "error") {
      errors.push(`[console.error] ${msg.text()}`);
    }
  });
  page.on("pageerror", (err) => {
    errors.push(`[uncaught exception] ${err.message}`);
  });
  page.on("requestfailed", (req) => {
    // Network-level failures (aborted/dns/etc.) are not JS console errors,
    // but a request that fails outright is still worth surfacing distinctly
    // from a real console.error; keep it in a separate bucket via prefix so
    // callers can filter if they expect some requests to fail (e.g. probing
    // a route that doesn't exist).
    const failure = req.failure();
    if (failure && !req.url().includes("/favicon")) {
      errors.push(`[request-failed] ${req.method()} ${req.url()} :: ${failure.errorText}`);
    }
  });

  return {
    getErrors: () => errors.slice(),
    clear: () => {
      errors.length = 0;
    },
  };
}

export async function assertNoConsoleErrors(
  page: Page,
  testInfo: TestInfo,
  screen: string,
  errors: string[],
) {
  if (errors.length > 0) {
    await recordFinding(page, testInfo, {
      screen,
      expected: "No uncaught console errors / exceptions while using the screen",
      actual: `${errors.length} error(s): ${errors.slice(0, 5).join(" | ")}`,
      severity: "high",
    });
  }
  expect(errors, `Console/page errors on ${screen}: ${errors.join("\n")}`).toEqual([]);
}

/**
 * Scans the page for any element whose OWN direct text content (not the
 * concatenation of descendants) is exactly one of BAD_TEXT_VALUES. Returns
 * the list of offending selectors/snippets for reporting.
 */
export async function findBadTextNodes(page: Page): Promise<string[]> {
  return page.evaluate((badValues) => {
    const offenders: string[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node: Node | null;
    // eslint-disable-next-line no-cond-assign
    while ((node = walker.nextNode())) {
      const text = (node.textContent ?? "").trim();
      if (badValues.includes(text)) {
        const parent = node.parentElement;
        const tag = parent?.tagName?.toLowerCase() ?? "?";
        const cls = parent?.className ? `.${String(parent.className).split(" ")[0]}` : "";
        offenders.push(`<${tag}${cls}> "${text}"`);
      }
    }
    return offenders;
  }, BAD_TEXT_VALUES);
}

export async function assertNoBadText(page: Page, testInfo: TestInfo, screen: string) {
  const offenders = await findBadTextNodes(page);
  if (offenders.length > 0) {
    await recordFinding(page, testInfo, {
      screen,
      expected: 'No element text equal to "undefined"/"null"/"NaN"/"[object Object]"',
      actual: offenders.join(", "),
      severity: "high",
    });
  }
  expect(offenders, `Bad literal text rendered on ${screen}: ${offenders.join(", ")}`).toEqual([]);
}

/** document.scrollingElement.scrollWidth <= clientWidth + 1 (no horizontal page scroll). */
export async function assertNoHorizontalScroll(page: Page, testInfo: TestInfo, screen: string) {
  const overflow = await page.evaluate(() => {
    const el = document.scrollingElement as HTMLElement | null;
    if (!el) return null;
    return { scrollWidth: el.scrollWidth, clientWidth: el.clientWidth };
  });
  if (!overflow) return;
  const diff = overflow.scrollWidth - overflow.clientWidth;
  if (diff > 1) {
    await recordFinding(page, testInfo, {
      screen,
      expected: "document.scrollingElement.scrollWidth <= clientWidth + 1 (no page-level horizontal scroll)",
      actual: `scrollWidth=${overflow.scrollWidth} clientWidth=${overflow.clientWidth} (overflow ${diff}px)`,
      severity: "medium",
    });
  }
  expect(diff, `Horizontal page scroll on ${screen}: scrollWidth=${overflow.scrollWidth} clientWidth=${overflow.clientWidth}`).toBeLessThanOrEqual(1);
}
