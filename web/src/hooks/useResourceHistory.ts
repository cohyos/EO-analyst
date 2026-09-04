import { useEffect, useState } from "react";
import type { StatusResponse } from "@/types/api";

export interface ResourceSample {
  at: number; // epoch ms
  vram_used_mb: number;
  vram_total_mb: number;
  gpu_util_pct: number;
  gpu_temp_c: number;
  ram_free_mb: number;
  ram_total_mb: number;
}

export const HISTORY_WINDOW_MS = 30 * 60 * 1000; // 30 minutes
export const MAX_SAMPLES = 1000; // hard cap regardless of push frequency
const STORAGE_KEY = "eoa.resourceHistory.v1";

/**
 * Pure ring-buffer reducer: appends `sample`, drops anything older than the
 * 30-minute window (relative to the new sample's own timestamp so replaying
 * a persisted buffer against "now" doesn't evict everything), and caps the
 * array length. Exported standalone so it's unit-testable without a DOM.
 */
export function pushResourceSample(
  history: ResourceSample[],
  sample: ResourceSample,
): ResourceSample[] {
  const cutoff = sample.at - HISTORY_WINDOW_MS;
  const trimmed = history.filter((s) => s.at >= cutoff && s.at <= sample.at);
  const next = [...trimmed, sample];
  return next.length > MAX_SAMPLES ? next.slice(next.length - MAX_SAMPLES) : next;
}

/** Drops samples older than `nowMs - HISTORY_WINDOW_MS` without appending. */
export function pruneResourceHistory(history: ResourceSample[], nowMs: number): ResourceSample[] {
  const cutoff = nowMs - HISTORY_WINDOW_MS;
  return history.filter((s) => s.at >= cutoff);
}

export function sampleFromStatus(status: StatusResponse): ResourceSample {
  const atMs = Date.parse(status.at);
  return {
    at: Number.isNaN(atMs) ? Date.now() : atMs,
    vram_used_mb: status.gate.gpu.vram_used_mb,
    vram_total_mb: status.gate.gpu.vram_total_mb,
    gpu_util_pct: status.gate.gpu.util_pct,
    gpu_temp_c: status.gate.gpu.temp_c,
    ram_free_mb: status.gate.ram.free_mb,
    ram_total_mb: status.gate.ram.total_mb,
  };
}

function loadPersisted(): ResourceSample[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return pruneResourceHistory(parsed as ResourceSample[], Date.now());
  } catch {
    return [];
  }
}

function persist(history: ResourceSample[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(history));
  } catch {
    // storage full / unavailable (private mode) — history just stays in-memory
  }
}

/**
 * Client-side ring buffer of the last 30 minutes of resource-gate telemetry,
 * fed by every `/ws/status` push and persisted to localStorage so a page
 * reload (or a brief WS drop) doesn't blank the sparklines.
 */
export function useResourceHistory(status: StatusResponse | null): ResourceSample[] {
  const [history, setHistory] = useState<ResourceSample[]>(() => loadPersisted());
  const lastAt = status?.at;

  useEffect(() => {
    if (!status || !lastAt) return;
    const sample = sampleFromStatus(status);
    setHistory((prev) => {
      // Ignore a re-render with the same `at` (StrictMode double-invoke,
      // or a push that carried no new telemetry).
      if (prev.length > 0 && prev[prev.length - 1].at === sample.at) return prev;
      const next = pushResourceSample(prev, sample);
      persist(next);
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastAt]);

  return history;
}
