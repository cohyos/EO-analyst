import fs from "node:fs";
import path from "node:path";

/**
 * Runs once before the whole suite. Clears findings from a previous run so
 * QA_FINDINGS.md always reflects only the run that just happened.
 */
export default function globalSetup() {
  const reportDir = path.join(__dirname, "..", "report");
  fs.mkdirSync(reportDir, { recursive: true });
  const findingsFile = path.join(reportDir, "findings.jsonl");
  fs.writeFileSync(findingsFile, "", "utf-8");
}
