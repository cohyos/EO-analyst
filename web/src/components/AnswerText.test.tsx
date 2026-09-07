import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AnswerText } from "./AnswerText";
import type { CitationLike } from "./CitationText";

function renderWithRouter(ui: ReactElement) {
  return render(
    <MemoryRouter initialEntries={["/investigations/175"]}>
      <Routes>
        <Route path="/investigations/175" element={ui} />
        <Route path="/items/:id" element={<div data-testid="item-page">item page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AnswerText", () => {
  it("renders null (nothing) for empty/whitespace-only/null text", () => {
    expect(AnswerText({ text: "" })).toBe(null);
    expect(AnswerText({ text: "   " })).toBe(null);
    expect(AnswerText({ text: null })).toBe(null);
    expect(AnswerText({ text: undefined })).toBe(null);
  });

  it("renders a heading-less paragraph as a <p>, with citation chips still clickable", () => {
    const citations: CitationLike[] = [{ n: 1, item_id: 55, title: "מקור", url: "https://x.test" }];
    renderWithRouter(<AnswerText text="תשובה ישירה עם ציטוט [1]." citations={citations} />);
    const p = document.querySelector("p");
    expect(p).not.toBeNull();
    expect(screen.getByRole("button", { name: "1" })).toBeInTheDocument();
  });

  it("renders a '### <title>' section as an <h4>, and its '- ' lines as a real <ul>/<li> list", () => {
    renderWithRouter(<AnswerText text={"תשובה.\n\n### עובדות מרכזיות\n- עובדה א\n- עובדה ב"} />);
    const heading = screen.getByRole("heading", { level: 4, name: "עובדות מרכזיות" });
    expect(heading).toBeInTheDocument();
    const list = document.querySelector("ul");
    expect(list).not.toBeNull();
    expect(list!.querySelectorAll("li")).toHaveLength(2);
    expect(screen.getByText("עובדה א")).toBeInTheDocument();
    expect(screen.getByText("עובדה ב")).toBeInTheDocument();
  });

  it("never renders a '### מקורות' heading or its contents, even for a legacy pre-CR-invest.md answer", () => {
    renderWithRouter(
      <AnswerText text={"תשובה.\n\n### מקורות\n- [1] https://example.com/should-not-render"} />,
    );
    expect(screen.queryByText("מקורות")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("example.com/should-not-render");
  });

  it("wraps a bare Latin/number run in the rendered text in a dir=ltr <bdi> (no literal isolate glyphs)", () => {
    const { container } = renderWithRouter(<AnswerText text="המוצר Ophir Optronics נמכר בהצלחה." />);
    expect(container.querySelector('bdi[dir="ltr"]')).not.toBeNull();
    expect(container.textContent).toContain("Ophir Optronics");
  });

  it("clicking a citation chip inside a bullet still navigates (onOpenItem/navigate wiring survives the section parser)", () => {
    const onOpenItem = vi.fn();
    const citations: CitationLike[] = [{ n: 1, item_id: 7, title: "מקור", url: "https://x.test" }];
    renderWithRouter(
      <AnswerText
        text={"תשובה.\n\n### עובדות מרכזיות\n- עובדה עם ציטוט [1]"}
        citations={citations}
        onOpenItem={onOpenItem}
      />,
    );
    screen.getByRole("button", { name: "1" }).click();
    expect(onOpenItem).toHaveBeenCalledWith(7);
  });

  // CR-invest.md fixture: job 175's stored answer_he, verbatim.
  const JOB_175_ANSWER_HE =
    'המוצר החדש, ⁦Ophir® SupIR-X, ⁩הוא עדשת זום מוטורית רציפה (⁦Continuous Zoom⁩) בטווח ⁦15-300 ⁩מ\\"מ ' +
    "ובעדשה קבועה ⁦f/4, ⁩המיועדת ספציפית לגלאי ⁦MWIR ⁩מסוג ⁦10 µm SXGA.⁩\n\n" +
    "### עובדות מרכזיות\n" +
    "- העדשה מיועדת לגלאי ⁦MWIR ⁩מסוג ⁦10 µm SXGA ⁩המיועדים למשימות ⁦ISR [1]⁩\n" +
    "- העדשה כוללת מנגנון סגירת תריס מכני (⁦NUC shutter⁩) לשמירה על איכות התמונה [1,2]\n\n" +
    "### פערים / מה לא ידוע\n" +
    'אין נתונים ספציפיים על סכומי חוזה. אין אישור ישיר על קשר מסחרי עם תע\\"א.\n\n' +
    "### מקורות\n" +
    "- [⁦1⁩] ⁦https://hiwars.com/en/intel/the-all-new-15-300-mm-f4-mwir-zoom-engineered-for⁩";

  it("job 175 fixture: renders three headings, drops מקורות, and shows no raw isolate/backslash artifacts", () => {
    const citations: CitationLike[] = [
      { n: 1, item_id: null, title: "hiwars", url: "https://hiwars.com/x" },
    ];
    const { container } = renderWithRouter(
      <AnswerText text={JOB_175_ANSWER_HE} citations={citations} />,
    );
    expect(screen.getByRole("heading", { level: 4, name: "עובדות מרכזיות" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 4, name: "פערים / מה לא ידוע" })).toBeInTheDocument();
    expect(screen.queryByText("מקורות")).not.toBeInTheDocument();
    expect(container.textContent).not.toContain("hiwars.com/en/intel");
    expect(container.textContent).not.toMatch(/[⁦-⁩]/);
    expect(container.textContent).not.toContain('\\"');
    expect(container.textContent).toContain("מ״מ"); // gershayim, not a literal backslash-quote
  });

  it("job 175 fixture: every [1] citation marker (including the one split out of the [1,2] group) renders as a clickable chip", () => {
    const citations: CitationLike[] = [
      { n: 1, item_id: null, title: "hiwars", url: "https://hiwars.com/x" },
    ];
    renderWithRouter(<AnswerText text={JOB_175_ANSWER_HE} citations={citations} />);
    // Job 175's second bullet cites "[1,2]" (expanded to "[1][2]") and the first bullet cites
    // "[1]" on its own -- two separate "1" chips is the correct, expected result.
    expect(screen.getAllByRole("button", { name: "1" }).length).toBeGreaterThanOrEqual(2);
  });
});
