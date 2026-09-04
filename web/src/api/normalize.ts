// Shared coercion helpers + safe defaults for API payloads.
//
// The real backend can legitimately return null/partial objects — an empty
// database before the first nightly run, a report that hasn't been built
// yet, a status push before the resource gate initializes, etc. This module
// is the single place that knows the "zero" shape of every such object so
// web/src/api/real.ts and web/src/hooks/useStatusSocket.ts (which talks to
// /ws/status directly, outside the ApiClient) can both normalize incoming
// data into the shapes web/src/types/api.ts promises the rest of the app.
import type {
  NightSummary,
  PipelineStatus,
  ResourceGateStatus,
  StatusResponse,
} from "@/types/api";

export function arr<T>(value: T[] | null | undefined): T[] {
  return Array.isArray(value) ? value : [];
}

export function str(value: string | null | undefined, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

export function num(value: number | null | undefined, fallback = 0): number {
  return typeof value === "number" && !Number.isNaN(value) ? value : fallback;
}

export function bool(value: boolean | null | undefined, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

export function obj<T extends object>(value: T | null | undefined, fallback: T): T {
  return value && typeof value === "object" ? value : fallback;
}

export const ZERO_NIGHT_SUMMARY: NightSummary = {
  items_ingested: 0,
  classified: 0,
  red: 0,
  orange: 0,
  deep_searches: 0,
  duration_min: 0,
  errors: 0,
};

export function normalizeNightSummary(
  raw: Partial<NightSummary> | null | undefined,
): NightSummary {
  return {
    items_ingested: num(raw?.items_ingested),
    classified: num(raw?.classified),
    red: num(raw?.red),
    orange: num(raw?.orange),
    deep_searches: num(raw?.deep_searches),
    duration_min: num(raw?.duration_min),
    errors: num(raw?.errors),
  };
}

export const DEFAULT_GATE: ResourceGateStatus = {
  vram_used_mb: 0,
  vram_total_mb: 0,
  gpu_util_pct: 0,
  gpu_temp_c: 0,
  ram_used_mb: 0,
  ram_total_mb: 0,
  disk_free_gb: 0,
  loaded_model: null,
};

export const DEFAULT_PIPELINE: PipelineStatus = {
  current_job: null,
  queue_depth: 0,
  stage: null,
  night_window: false,
  next_run_at: null,
  last_run: null,
};

/** Normalizes a `/api/status` or `/ws/status` push into a guaranteed shape. */
export function normalizeStatus(
  raw: Partial<StatusResponse> | null | undefined,
): StatusResponse {
  return {
    at: str(raw?.at),
    services: {
      postgres: bool(raw?.services?.postgres),
      ollama: bool(raw?.services?.ollama),
      searxng: bool(raw?.services?.searxng),
      ntfy: bool(raw?.services?.ntfy),
    },
    gate: obj(raw?.gate, DEFAULT_GATE),
    pipeline: obj(raw?.pipeline, DEFAULT_PIPELINE),
  };
}
