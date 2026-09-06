import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { EntityDetail, EntitySummary } from "@/types/api";

const getEntities = vi.fn();
const getEntity = vi.fn();
const getGraph = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getEntities: (...args: unknown[]) => getEntities(...args),
    getEntity: (...args: unknown[]) => getEntity(...args),
    getGraph: (...args: unknown[]) => getGraph(...args),
  },
  USE_MOCKS: false,
}));

import { EntitiesPage } from "./EntitiesPage";

function makeEntity(id: number, name: string, overrides: Partial<EntitySummary> = {}): EntitySummary {
  return {
    id,
    name,
    kind: "company",
    country: "IL",
    aliases: [],
    focus: ["airborne_pods"],
    item_count: 6,
    last_seen: "2026-09-03T18:40:00+03:00",
    relevance: 0.8,
    is_watchlist: false,
    mentions_7d: 3,
    mentions_30d: 6,
    is_israeli: false,
    ...overrides,
  };
}

const ENTITIES = [
  makeEntity(1, "ישות ישראלית", { is_israeli: true, country: "IL" }),
  makeEntity(2, "Foreign Entity", { is_israeli: false, country: "US" }),
];

function renderEntitiesPage(initialPath = "/entities") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/entities" element={<EntitiesPage />} />
          <Route path="/entities/:id" element={<EntitiesPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getEntities.mockReset();
  getEntity.mockReset();
  getGraph.mockReset();
  getEntities.mockResolvedValue(ENTITIES);
  const detail: EntityDetail = {
    ...ENTITIES[0],
    timeline: [],
    business_events: [],
    kpis: { mentions_7d: 3, mentions_30d: 6, events_count: 0, related_items_by_level: {} },
    edge_groups: [],
    neighbors: [],
  };
  getEntity.mockResolvedValue(detail);
  getGraph.mockResolvedValue({ nodes: [], edges: [] });
});

// A13 (מיקוד תעשייה ישראלית, docs/PLAN_WINDOWS_NATIVE.md): the "ישראליות בלבד" filter chip
// toggles `israel=true` on GET /api/entities, same UX/wiring as the existing "watchlist" chip.
describe("EntitiesPage Israel focus filter (A13)", () => {
  it("plain /entities passes no israel filter by default", async () => {
    renderEntitiesPage();
    await waitFor(() => expect(getEntities).toHaveBeenCalled());
    const lastCall = getEntities.mock.calls.at(-1)![0];
    expect(lastCall.israel).toBeUndefined();
  });

  it("toggling the Israel filter checkbox adds israel=true to the query, and toggling again removes it", async () => {
    renderEntitiesPage();
    await waitFor(() => expect(getEntities).toHaveBeenCalled());

    const checkbox = screen.getByTestId("israel-filter-toggle").querySelector("input")!;
    expect(checkbox).not.toBeChecked();

    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();
    await waitFor(() =>
      expect(getEntities).toHaveBeenCalledWith(expect.objectContaining({ israel: true })),
    );

    fireEvent.click(checkbox);
    expect(checkbox).not.toBeChecked();
    await waitFor(() => {
      const lastCall = getEntities.mock.calls.at(-1)![0];
      expect(lastCall.israel).toBeUndefined();
    });
  });

  it("shows the 🇮🇱 flag badge on an entity row when is_israeli is true, and not otherwise", async () => {
    renderEntitiesPage();
    await waitFor(() => expect(getEntities).toHaveBeenCalled());

    expect(await screen.findByTestId("entity-row-israel-badge-1")).toBeInTheDocument();
    expect(screen.queryByTestId("entity-row-israel-badge-2")).not.toBeInTheDocument();
  });

  it("shows the 🇮🇱 flag badge on the selected entity card when is_israeli is true", async () => {
    renderEntitiesPage("/entities/1");
    expect(await screen.findByTestId("entity-card-israel-badge")).toBeInTheDocument();
  });

  it("does not show the flag badge on the selected entity card when is_israeli is false", async () => {
    getEntity.mockResolvedValue({
      ...ENTITIES[1],
      timeline: [],
      business_events: [],
      kpis: { mentions_7d: 0, mentions_30d: 0, events_count: 0, related_items_by_level: {} },
      edge_groups: [],
      neighbors: [],
    });
    renderEntitiesPage("/entities/2");
    await screen.findByRole("heading", { name: "Foreign Entity" });
    expect(screen.queryByTestId("entity-card-israel-badge")).not.toBeInTheDocument();
  });
});
