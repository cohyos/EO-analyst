import { describe, expect, it } from "vitest";
import { formatDate, formatDateTime, formatDuration, formatTime } from "./time";

const LRI = "⁦";
const PDI = "⁩";

// Content review (docs/qa/content_review/CR-ui.md): these formatters return a short
// numeric/punctuation string with no strong-direction character of its own. Dropped as plain
// text into the app's RTL flow (most call sites are a bare `{formatDateTime(x)}`, no `<bdi>`
// wrapper), the bidi algorithm has nothing to anchor the run to and can reorder/line-wrap it
// oddly (a separating comma stranded on its own line, ahead of the date it belongs after).
// Wrapping in LRI/PDI isolates the run as LTR wherever it lands -- invisible in the rendered
// text and in a copy-paste, so every call site keeps working with a plain `{fn(x)}`.
describe("time formatters isolate their output for RTL contexts", () => {
  it("formatDateTime wraps its result in LRI/PDI", () => {
    const out = formatDateTime("2026-09-07T11:12:00+03:00");
    expect(out.startsWith(LRI)).toBe(true);
    expect(out.endsWith(PDI)).toBe(true);
    expect(out).toContain("11:12");
  });

  it("formatDate wraps its result in LRI/PDI", () => {
    const out = formatDate("2026-09-07T11:12:00+03:00");
    expect(out.startsWith(LRI)).toBe(true);
    expect(out.endsWith(PDI)).toBe(true);
  });

  it("formatTime wraps its result in LRI/PDI", () => {
    const out = formatTime("2026-09-07T11:12:00+03:00");
    expect(out.startsWith(LRI)).toBe(true);
    expect(out.endsWith(PDI)).toBe(true);
  });

  it("formatDuration wraps its result in LRI/PDI", () => {
    const out = formatDuration("2026-09-07T11:00:00Z", "2026-09-07T11:05:30Z");
    expect(out).not.toBeNull();
    expect(out!.startsWith(LRI)).toBe(true);
    expect(out!.endsWith(PDI)).toBe(true);
    expect(out).toContain("5:30");
  });

  it("still returns the em-dash placeholder for missing input, unwrapped", () => {
    expect(formatDateTime(null)).toBe("—");
    expect(formatDate(undefined)).toBe("—");
    expect(formatTime(null)).toBe("—");
    expect(formatDuration(null, null)).toBeNull();
  });
});
