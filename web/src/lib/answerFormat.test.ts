import { describe, expect, it } from "vitest";
import {
  expandGroupedCitationMarkers,
  normalizeHebrewPunctuation,
  parseAnswerSections,
} from "./answerFormat";

describe("normalizeHebrewPunctuation", () => {
  it("drops a stray backslash before a quote, leaving the quote for the gershayim pass", () => {
    expect(normalizeHebrewPunctuation('כטב\\"ם')).toBe("כטב״ם");
  });

  it("converts an ASCII quote between two Hebrew letters to a gershayim", () => {
    expect(normalizeHebrewPunctuation('מ"מ')).toBe("מ״מ");
  });

  it("converts an ASCII apostrophe after a Hebrew letter to a geresh", () => {
    expect(normalizeHebrewPunctuation("שו'ר")).toBe("שו׳ר");
  });

  it("strips embedded bidi-isolate control characters", () => {
    expect(normalizeHebrewPunctuation("⁦Ophir®⁩ הוא")).toBe("Ophir® הוא");
  });

  it("collapses doubled ASCII quotes (and the resulting single quote still becomes a gershayim between Hebrew letters)", () => {
    expect(normalizeHebrewPunctuation('ארה""ב')).toBe("ארה״ב");
  });

  it("collapses a stray space before closing sentence punctuation", () => {
    expect(normalizeHebrewPunctuation("עובדה [1, 5] .")).toBe("עובדה [1, 5].");
  });

  it("passes through null/undefined/empty as an empty string", () => {
    expect(normalizeHebrewPunctuation(null)).toBe("");
    expect(normalizeHebrewPunctuation(undefined)).toBe("");
    expect(normalizeHebrewPunctuation("")).toBe("");
  });

  it("is idempotent", () => {
    const once = normalizeHebrewPunctuation('⁦Ophir®⁩ מ"מ כטב\\"ם');
    expect(normalizeHebrewPunctuation(once)).toBe(once);
  });
});

describe("expandGroupedCitationMarkers", () => {
  it("splits a grouped citation marker into individual adjacent markers", () => {
    expect(expandGroupedCitationMarkers("עובדה [1,2]")).toBe("עובדה [1][2]");
  });

  it("handles a group with spaces after the commas", () => {
    expect(expandGroupedCitationMarkers("עובדה [1, 2, 3]")).toBe("עובדה [1][2][3]");
  });

  it("leaves a lone citation marker untouched", () => {
    expect(expandGroupedCitationMarkers("עובדה [1]")).toBe("עובדה [1]");
  });

  it("leaves text with no citation markers untouched", () => {
    expect(expandGroupedCitationMarkers("אין כאן כלום")).toBe("אין כאן כלום");
  });
});

describe("parseAnswerSections", () => {
  it("returns an empty array for empty/whitespace-only input", () => {
    expect(parseAnswerSections("")).toEqual([]);
    expect(parseAnswerSections("   \n\n  ")).toEqual([]);
    expect(parseAnswerSections(null)).toEqual([]);
    expect(parseAnswerSections(undefined)).toEqual([]);
  });

  it("a lone paragraph with no headings becomes one title-less section with one paragraph block", () => {
    const sections = parseAnswerSections("תשובה ישירה כלשהי.");
    expect(sections).toEqual([{ title: null, blocks: [{ type: "paragraph", text: "תשובה ישירה כלשהי." }] }]);
  });

  it("a '### <title>' heading followed by '- ' lines becomes a list block under that title", () => {
    const sections = parseAnswerSections("תשובה.\n\n### עובדות מרכזיות\n- עובדה א [1]\n- עובדה ב [2]");
    expect(sections).toHaveLength(2);
    expect(sections[1]).toEqual({
      title: "עובדות מרכזיות",
      blocks: [{ type: "list", items: ["עובדה א [1]", "עובדה ב [2]"] }],
    });
  });

  it("a '### הקשר' section spanning multiple blank-line-separated paragraphs keeps them all under one heading", () => {
    const sections = parseAnswerSections(
      "תשובה ישירה.\n\n### הקשר\nפסקה ראשונה של ההקשר.\n\nפסקה שנייה של ההקשר.",
    );
    const context = sections.find((s) => s.title === "הקשר");
    expect(context).toBeDefined();
    expect(context!.blocks).toEqual([
      { type: "paragraph", text: "פסקה ראשונה של ההקשר." },
      { type: "paragraph", text: "פסקה שנייה של ההקשר." },
    ]);
  });

  it("drops a '### מקורות' section entirely (legacy pre-CR-invest.md answers only -- the current backend never emits it)", () => {
    const sections = parseAnswerSections("תשובה.\n\n### מקורות\n- [1] https://example.com/a");
    expect(sections.some((s) => s.title === "מקורות")).toBe(false);
    expect(sections.some((s) => JSON.stringify(s).includes("example.com"))).toBe(false);
  });

  it("expands a grouped citation marker before parsing, so a bullet keeps both numbers", () => {
    const sections = parseAnswerSections("תשובה.\n\n### עובדות מרכזיות\n- עובדה משותפת [1,2]");
    const facts = sections.find((s) => s.title === "עובדות מרכזיות");
    expect(facts!.blocks).toEqual([{ type: "list", items: ["עובדה משותפת [1][2]"] }]);
  });

  // CR-invest.md fixture: job 175's stored answer_he, verbatim (isolate marks and stray
  // backslash-quotes included) -- the exact case the user reported.
  const JOB_175_ANSWER_HE =
    'המוצר החדש, ⁦Ophir® SupIR-X, ⁩הוא עדשת זום מוטורית רציפה (⁦Continuous Zoom⁩) בטווח ⁦15-300 ⁩מ\\"מ ' +
    "ובעדשה קבועה ⁦f/4, ⁩המיועדת ספציפית לגלאי ⁦MWIR ⁩מסוג ⁦10 µm SXGA. ⁩העדשה מיוצרת על ידי חברת " +
    "⁦Ophir Optronics (⁩שייכת לקונצרניט ⁦MKS Instruments) ⁩ומיועדת למשימות ⁦ISR (⁩מודיעין, תצפית " +
    "וסימון⁦) ⁩במרחקים ארוכים באוויר, ביבשה ובים.\n\n" +
    "### עובדות מרכזיות\n" +
    "- העדשה מיועדת לגלאי ⁦MWIR ⁩מסוג ⁦10 µm SXGA ⁩המיועדים למשימות ⁦ISR [1]⁩\n" +
    '- העדשה מציעה טווח זום רציף של ⁦15-300 ⁩מ\\"מ עם פתיחת עדשה קבועה של ⁦f/4 [1,2]⁩\n\n' +
    "### הקשר\n" +
    "מבחינה טכנולוגית, העדשה מהווה קפיצת מדרגה (⁦Generational Leap⁩) בתחום ה-⁦EO/IR.⁩\n\n" +
    "### פערים / מה לא ידוע\n" +
    'אין נתונים ספציפיים על סכומי חוזה. אין אישור ישיר על קשר מסחרי עם תע\\"א.\n\n' +
    "### מקורות\n" +
    "- [⁦1⁩] ⁦https://hiwars.com/en/intel/the-all-new-15-300-mm-f4-mwir-zoom-engineered-for⁩";

  it("job 175 fixture: parses into the four expected sections, מקורות dropped", () => {
    const sections = parseAnswerSections(JOB_175_ANSWER_HE);
    expect(sections.map((s) => s.title)).toEqual([null, "עובדות מרכזיות", "הקשר", "פערים / מה לא ידוע"]);
  });

  it("job 175 fixture: no bidi-isolate control characters or stray backslashes survive anywhere", () => {
    const sections = parseAnswerSections(JOB_175_ANSWER_HE);
    const serialized = JSON.stringify(sections);
    expect(serialized).not.toMatch(/[⁦-⁩]/);
    expect(serialized).not.toContain('\\"');
  });

  it("job 175 fixture: the grouped [1,2] marker is expanded to two individual markers", () => {
    const sections = parseAnswerSections(JOB_175_ANSWER_HE);
    const facts = sections.find((s) => s.title === "עובדות מרכזיות");
    const groupedBullet = facts!.blocks.find(
      (b) => b.type === "list" && b.items.some((i) => i.includes("15-300")),
    );
    expect(groupedBullet).toBeDefined();
    const item = (groupedBullet as { type: "list"; items: string[] }).items.find((i) =>
      i.includes("15-300"),
    )!;
    expect(item).toContain("[1][2]");
    expect(item).not.toContain("[1,2]");
  });

  it("job 175 fixture: the stray backslash-quote becomes a proper gershayim", () => {
    const sections = parseAnswerSections(JOB_175_ANSWER_HE);
    const serialized = JSON.stringify(sections);
    expect(serialized).toContain("מ״מ");
  });
});
