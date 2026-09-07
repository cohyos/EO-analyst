import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { AskCitation } from "@/types/api";
import { AskSourcesFooter } from "./AskSourcesFooter";

const sources: AskCitation[] = [
  {
    n: 1,
    item_id: 101,
    title: "Elbit Systems חושפת דור חדש",
    url: "https://example.test/1",
    level: "orange",
    source_name: "Globes",
    note: "מתאר ישירות את הפוד החדש",
  },
];

describe("AskSourcesFooter", () => {
  it("renders nothing when there are no sources", () => {
    const { container } = render(
      <MemoryRouter>
        <AskSourcesFooter sources={[]} />
      </MemoryRouter>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the compact header with a count, title, source name and level badge", () => {
    render(
      <MemoryRouter>
        <AskSourcesFooter sources={sources} />
      </MemoryRouter>,
    );
    expect(screen.getByText("מקורות (1)")).toBeInTheDocument();
    expect(screen.getByText("Elbit Systems חושפת דור חדש")).toBeInTheDocument();
    expect(screen.getByText("Globes")).toBeInTheDocument();
    expect(screen.getByText("חשוב")).toBeInTheDocument(); // orange level label
  });

  it("keeps the relevance note collapsed until the toggle is clicked", () => {
    render(
      <MemoryRouter>
        <AskSourcesFooter sources={sources} />
      </MemoryRouter>,
    );
    expect(screen.queryByText("מתאר ישירות את הפוד החדש")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "הצג הערת רלוונטיות" }));
    expect(screen.getByText("מתאר ישירות את הפוד החדש")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "הסתר הערת רלוונטיות" }));
    expect(screen.queryByText("מתאר ישירות את הפוד החדש")).not.toBeInTheDocument();
  });

  // CORR (cross-source corroboration, 2026-09-07): `AskCitation.corroboration` is additive and
  // absent until the backend enriches citations with it -- the badge must not appear at all for
  // an ordinary (pre-CORR) citation, and must appear once the field is present.
  it("shows no corroboration badge under a source when the field is absent", () => {
    render(
      <MemoryRouter>
        <AskSourcesFooter sources={sources} />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId(/corroboration-badge-/)).not.toBeInTheDocument();
  });

  it("shows the corroboration badge under a source once the backend enriches the citation with it", () => {
    const withCorr: AskCitation[] = [
      {
        ...sources[0],
        corroboration: { status: "corroborated", count: 2, sources: [], checked_at: null },
      },
    ];
    render(
      <MemoryRouter>
        <AskSourcesFooter sources={withCorr} />
      </MemoryRouter>,
    );
    expect(screen.getByText("מאומת ב-2 מקורות")).toBeInTheDocument();
  });
});
