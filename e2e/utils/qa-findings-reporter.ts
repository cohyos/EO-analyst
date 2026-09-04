import type { Reporter, TestCase, TestResult, FullResult } from "@playwright/test/reporter";
import fs from "node:fs";
import path from "node:path";
import type { Finding } from "./helpers";

/**
 * Custom Playwright reporter that produces e2e/QA_FINDINGS.md.
 *
 * Two sources feed the report:
 *  1. Explicit findings recorded mid-test via recordFinding()/recordFindingSync()
 *     in utils/helpers.ts, appended as JSON-lines to report/findings.jsonl.
 *  2. Every FAILED test case, picked up here via onTestEnd — a failure is a
 *     finding even if the test itself never called recordFinding.
 *
 * Headings are Hebrew per spec ("written in Hebrew headings/English
 * details"); the finding bodies (expected/actual/error text) stay in
 * English since that's how Playwright/JS report them.
 */

const SEVERITY_LABEL_HE: Record<Finding["severity"], string> = {
  critical: "קריטי",
  high: "גבוה",
  medium: "בינוני",
  low: "נמוך",
};

const SEVERITY_ORDER: Finding["severity"][] = ["critical", "high", "medium", "low"];

interface FailureFinding {
  screen: string;
  expected: string;
  actual: string;
  severity: Finding["severity"];
  screenshotPath?: string;
  project: string;
  testTitle: string;
  fromFailure: true;
}

export default class QaFindingsReporter implements Reporter {
  private failures: FailureFinding[] = [];
  private rootDir: string;

  constructor() {
    this.rootDir = path.join(__dirname, "..");
  }

  onTestEnd(test: TestCase, result: TestResult) {
    if (result.status === "passed" || result.status === "skipped") return;

    const screenshotAttachment = result.attachments.find((a) => a.name === "screenshot");
    const screen = test.titlePath().slice(2, -1).join(" / ") || test.parent.title || "unknown";

    this.failures.push({
      screen,
      expected: "Test passes",
      actual: (result.error?.message ?? result.status).slice(0, 2000),
      severity: result.status === "timedOut" ? "high" : "critical",
      screenshotPath: screenshotAttachment?.path,
      project: test.parent.project()?.name ?? "unknown",
      testTitle: test.title,
      fromFailure: true,
    });
  }

  onEnd(_result: FullResult) {
    const findingsFile = path.join(this.rootDir, "report", "findings.jsonl");
    let recorded: Finding[] = [];
    if (fs.existsSync(findingsFile)) {
      recorded = fs
        .readFileSync(findingsFile, "utf-8")
        .split("\n")
        .filter(Boolean)
        .map((line) => {
          try {
            return JSON.parse(line) as Finding;
          } catch {
            return null;
          }
        })
        .filter((x): x is Finding => x !== null);
    }

    const all: (Finding | FailureFinding)[] = [...recorded, ...this.failures];

    const md = this.render(all);
    fs.writeFileSync(path.join(this.rootDir, "QA_FINDINGS.md"), md, "utf-8");
  }

  private render(all: (Finding | FailureFinding)[]): string {
    const lines: string[] = [];
    lines.push("# ממצאי בדיקת איכות UI — EO-Analyst (Playwright)");
    lines.push("");
    lines.push(`נוצר אוטומטית: ${new Date().toISOString()}`);
    lines.push("");
    lines.push(
      `סה"כ ממצאים: **${all.length}** (כשלים בבדיקה: ${this.count(all, "fromFailure")}, ` +
        `ממצאים שנרשמו במפורש בתוך בדיקה שעברה: ${all.length - this.count(all, "fromFailure")})`,
    );
    lines.push("");

    if (all.length === 0) {
      lines.push("## אין ממצאים");
      lines.push("");
      lines.push("כל הבדיקות עברו ולא נרשם אף ממצא UI במפורש.");
      return lines.join("\n");
    }

    for (const severity of SEVERITY_ORDER) {
      const group = all.filter((f) => f.severity === severity);
      if (group.length === 0) continue;
      lines.push(`## חומרה: ${SEVERITY_LABEL_HE[severity]} (${severity}) — ${group.length}`);
      lines.push("");
      for (const [i, f] of group.entries()) {
        const isFailure = "fromFailure" in f;
        lines.push(`### ${i + 1}. ${f.screen} — ${f.testTitle}`);
        lines.push("");
        lines.push(`- **Type**: ${isFailure ? "test failure" : "explicit finding"}`);
        lines.push(`- **Project/viewport**: ${f.project}`);
        lines.push(`- **Expected**: ${this.oneLine(f.expected)}`);
        lines.push(`- **Actual**: ${this.oneLine(f.actual)}`);
        lines.push(
          `- **Screenshot**: ${
            f.screenshotPath ? "`" + path.relative(this.rootDir, f.screenshotPath) + "`" : "(none captured)"
          }`,
        );
        lines.push("");
      }
    }

    return lines.join("\n");
  }

  private oneLine(s: string): string {
    // eslint-disable-next-line no-control-regex
    const noAnsi = s.replace(/\x1b\[[0-9;]*m/g, "");
    return noAnsi.replace(/\s+/g, " ").trim().slice(0, 600);
  }

  private count(all: (Finding | FailureFinding)[], key: "fromFailure"): number {
    return all.filter((f) => key in f).length;
  }
}
