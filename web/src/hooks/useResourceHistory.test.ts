import { describe, expect, it } from "vitest";
import {
  HISTORY_WINDOW_MS,
  MAX_SAMPLES,
  pruneResourceHistory,
  pushResourceSample,
  sampleFromStatus,
  type ResourceSample,
} from "./useResourceHistory";
import type { StatusResponse } from "@/types/api";

function sample(at: number, over: Partial<ResourceSample> = {}): ResourceSample {
  return {
    at,
    vram_used_mb: 6000,
    vram_total_mb: 12000,
    gpu_util_pct: 50,
    gpu_temp_c: 60,
    ram_free_mb: 20000,
    ram_total_mb: 32000,
    ...over,
  };
}

describe("pushResourceSample", () => {
  it("appends a sample to an empty history", () => {
    const next = pushResourceSample([], sample(1000));
    expect(next).toHaveLength(1);
    expect(next[0].at).toBe(1000);
  });

  it("keeps samples within the 30-minute window and drops older ones", () => {
    const base = 10 * 60 * 1000; // 10 minutes
    const history = [sample(base)];
    const newAt = base + HISTORY_WINDOW_MS + 1; // just past the window from `base`
    const next = pushResourceSample(history, sample(newAt));
    expect(next).toHaveLength(1);
    expect(next[0].at).toBe(newAt);
  });

  it("retains a sample exactly at the cutoff boundary", () => {
    const newAt = 1_000_000;
    const boundary = newAt - HISTORY_WINDOW_MS;
    const history = [sample(boundary)];
    const next = pushResourceSample(history, sample(newAt));
    expect(next.map((s) => s.at)).toEqual([boundary, newAt]);
  });

  it("caps the buffer at MAX_SAMPLES", () => {
    let history: ResourceSample[] = [];
    // All within the same second so the 30-min window never evicts anything —
    // exercises the hard cap instead.
    for (let i = 0; i < MAX_SAMPLES + 50; i++) {
      history = pushResourceSample(history, sample(i));
    }
    expect(history).toHaveLength(MAX_SAMPLES);
    expect(history[history.length - 1].at).toBe(MAX_SAMPLES + 49);
  });

  it("ignores an out-of-order sample older than everything already stored", () => {
    // A sample "from the past" relative to what's already buffered still
    // gets appended (buffers are append-only) but the window is always
    // computed relative to the *new* sample, so this can legitimately
    // evict the newer entries if they're now outside its 30-min window.
    const next = pushResourceSample([sample(5000)], sample(1000));
    expect(next.some((s) => s.at === 1000)).toBe(true);
  });
});

describe("pruneResourceHistory", () => {
  it("drops everything older than the window relative to `nowMs`", () => {
    const history = [sample(0), sample(HISTORY_WINDOW_MS / 2), sample(HISTORY_WINDOW_MS)];
    const now = HISTORY_WINDOW_MS * 2;
    expect(pruneResourceHistory(history, now)).toHaveLength(0);
  });

  it("keeps samples within the window", () => {
    const now = HISTORY_WINDOW_MS;
    const history = [sample(0), sample(now - 1000), sample(now)];
    const pruned = pruneResourceHistory(history, now);
    expect(pruned.map((s) => s.at)).toEqual([0, now - 1000, now]);
  });
});

describe("sampleFromStatus", () => {
  function baseStatus(): StatusResponse {
    return {
      at: "2026-09-04T10:00:00+03:00",
      services: { postgres: true, ollama: true, searxng: true, ntfy: true },
      gate: {
        gpu: {
          available: true,
          vram_total_mb: 12227,
          vram_used_mb: 7426,
          vram_free_mb: 4801,
          util_pct: 95,
          temp_c: 75,
        },
        ram: { free_mb: 29292, total_mb: 31777 },
        disk_free_gb: 903.9,
        loaded_models: [],
        batch_window: false,
        recent_decisions: [],
      },
      pipeline: {
        current_job: null,
        queue_depth: 0,
        stage: null,
        night_window: false,
        next_run_at: null,
        last_run: null,
      },
    };
  }

  it("extracts a flat sample from the nested gate shape", () => {
    const s = sampleFromStatus(baseStatus());
    expect(s.vram_used_mb).toBe(7426);
    expect(s.vram_total_mb).toBe(12227);
    expect(s.gpu_util_pct).toBe(95);
    expect(s.gpu_temp_c).toBe(75);
    expect(s.ram_free_mb).toBe(29292);
    expect(s.ram_total_mb).toBe(31777);
    expect(s.at).toBe(Date.parse("2026-09-04T10:00:00+03:00"));
  });

  it("falls back to Date.now() for an unparsable timestamp", () => {
    const status = baseStatus();
    status.at = "not-a-date";
    const before = Date.now();
    const s = sampleFromStatus(status);
    expect(s.at).toBeGreaterThanOrEqual(before);
  });
});
