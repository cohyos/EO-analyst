import type { ApiClient } from "./types";
import { realApi } from "./real";
import { mockApi } from "@/mocks/mockApi";

export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === "true";

export const api: ApiClient = USE_MOCKS ? mockApi : realApi;

export type * from "./types";
