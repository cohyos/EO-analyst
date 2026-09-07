import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DEFAULT_GRAPH_FILTERS, GraphFilterBar, type GraphFilterState } from "./GraphFilterBar";

function setup(filters: GraphFilterState = DEFAULT_GRAPH_FILTERS, countries: string[] = ["IL", "US"]) {
  const onChange = vi.fn();
  render(<GraphFilterBar filters={filters} onChange={onChange} availableCountries={countries} />);
  return { onChange };
}

describe("GraphFilterBar", () => {
  it("toggling an entity-kind chip adds it to filters.kinds", () => {
    const { onChange } = setup();
    fireEvent.click(screen.getByRole("button", { name: "חברה" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ kinds: ["company"] }),
    );
  });

  it("toggling an already-selected kind chip removes it", () => {
    const { onChange } = setup({ ...DEFAULT_GRAPH_FILTERS, kinds: ["company"] });
    fireEvent.click(screen.getByRole("button", { name: "חברה" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ kinds: [] }));
  });

  it("toggling a relation-type chip adds it to filters.relationTypes", () => {
    const { onChange } = setup();
    fireEvent.click(screen.getByRole("button", { name: "שותף של" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ relationTypes: ["PARTNER_OF"] }),
    );
  });

  it("renders every available country as an option", () => {
    setup();
    expect(screen.getByRole("option", { name: /IL|ישראל/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /US|ארה"ב|ארצות/ })).toBeInTheDocument();
  });

  it("changing the since-window select passes the numeric day count", () => {
    const { onChange } = setup();
    fireEvent.change(screen.getByDisplayValue("כל הזמנים"), { target: { value: "30" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ sinceDays: 30 }));
  });

  it("shows a clear-filters button only when a filter is active", () => {
    setup();
    expect(screen.queryByRole("button", { name: "נקה סינון" })).not.toBeInTheDocument();

    setup({ ...DEFAULT_GRAPH_FILTERS, country: "IL" });
    expect(screen.getByRole("button", { name: "נקה סינון" })).toBeInTheDocument();
  });

  it("clear-filters resets to DEFAULT_GRAPH_FILTERS", () => {
    const { onChange } = setup({ ...DEFAULT_GRAPH_FILTERS, kinds: ["company"], country: "IL" });
    fireEvent.click(screen.getByRole("button", { name: "נקה סינון" }));
    expect(onChange).toHaveBeenCalledWith(DEFAULT_GRAPH_FILTERS);
  });
});
