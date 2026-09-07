import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProductLineFilter } from "./ProductLineFilter";

describe("ProductLineFilter (PL-ui)", () => {
  it("opens a popover listing all six product lines", async () => {
    const user = userEvent.setup();
    render(<ProductLineFilter value={[]} onChange={vi.fn()} />);

    await user.click(screen.getByTestId("product-line-filter-toggle"));
    const menu = screen.getByTestId("product-line-filter-menu");
    expect(menu).toBeInTheDocument();
    expect(menu.querySelectorAll("button")).toHaveLength(6);
  });

  it("calls onChange with the newly selected id when a product line is toggled on", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ProductLineFilter value={[]} onChange={onChange} />);

    await user.click(screen.getByTestId("product-line-filter-toggle"));
    await user.click(screen.getByText("פודי ציון מטרות / תקיפה"));

    expect(onChange).toHaveBeenCalledWith(["targeting_pods"]);
  });

  it("calls onChange with the id removed when an already-selected product line is toggled off", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ProductLineFilter value={["targeting_pods"]} onChange={onChange} />);

    await user.click(screen.getByTestId("product-line-filter-toggle"));
    const menu = screen.getByTestId("product-line-filter-menu");
    await user.click(within(menu).getByText("פודי ציון מטרות / תקיפה"));

    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("shows a clear control once at least one product line is selected", async () => {
    const user = userEvent.setup();
    render(<ProductLineFilter value={["mws_eo"]} onChange={vi.fn()} />);

    await user.click(screen.getByTestId("product-line-filter-toggle"));
    expect(screen.getByText("נקה סינון קווי מוצר")).toBeInTheDocument();
  });
});
