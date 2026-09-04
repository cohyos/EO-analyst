import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusStrip } from "./StatusStrip";
import type { StatusSocketState } from "@/hooks/useStatusSocket";
import type { StatusResponse } from "@/types/api";

const baseStatus: StatusResponse = {
  at: "2026-09-04T10:00:00+03:00",
  services: { postgres: true, ollama: true, searxng: true, ntfy: false },
  gate: {
    vram_used_mb: 6144,
    vram_total_mb: 12288,
    gpu_util_pct: 55,
    gpu_temp_c: 62,
    ram_used_mb: 28_160,
    ram_total_mb: 64_000,
    disk_free_gb: 200,
    loaded_model: "qwen2.5:14b-instruct",
  },
  pipeline: {
    current_job: null,
    queue_depth: 2,
    stage: "triage",
    night_window: true,
    next_run_at: null,
    last_run: null,
  },
};

describe("StatusStrip", () => {
  it("parses the status payload into VRAM/GPU/RAM/disk readouts", () => {
    const state: StatusSocketState = { status: baseStatus, connected: true, logs: [] };
    render(<StatusStrip state={state} />);

    // VRAM meter: 6144/12288 = 50%
    expect(screen.getByText("50%")).toBeInTheDocument();
    expect(screen.getByText("6.0/12.0 GB")).toBeInTheDocument();
    expect(screen.getByText("GPU 55%")).toBeInTheDocument();
    expect(screen.getByText("62°C")).toBeInTheDocument();
    expect(screen.getByText(/דיסק 200 GB פנוי/)).toBeInTheDocument();
    expect(screen.getByText("qwen2.5:14b-instruct")).toBeInTheDocument();
    expect(screen.getByText(/תור: 2/)).toBeInTheDocument();
    expect(screen.getByText("triage")).toBeInTheDocument();
  });

  it("shows a disconnected state instead of stale readouts when the socket drops", () => {
    const state: StatusSocketState = { status: null, connected: false, logs: [] };
    render(<StatusStrip state={state} />);
    expect(screen.getByText(/מנותק מהשרת/)).toBeInTheDocument();
    expect(screen.queryByText(/GPU/)).not.toBeInTheDocument();
  });

  it("renders a service dot per service in the status payload", () => {
    const state: StatusSocketState = { status: baseStatus, connected: true, logs: [] };
    render(<StatusStrip state={state} />);
    expect(screen.getByText("PG")).toBeInTheDocument();
    expect(screen.getByText("Ollama")).toBeInTheDocument();
    expect(screen.getByText("SearXNG")).toBeInTheDocument();
    expect(screen.getByText("ntfy")).toBeInTheDocument();
  });
});
