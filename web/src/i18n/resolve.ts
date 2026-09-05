import type { Dictionary, TranslationKey, TranslationParams } from "./types";

/** Reads a dot-joined path (e.g. `"feed.showingStatus"`) out of a dictionary object. */
export function resolvePath(dict: Dictionary, path: string): string {
  const value = path
    .split(".")
    .reduce<unknown>((acc, key) => (acc && typeof acc === "object" ? (acc as Record<string, unknown>)[key] : undefined), dict);
  return typeof value === "string" ? value : path;
}

/** Replaces `{name}` placeholders in a resolved string with `params[name]`. */
export function interpolate(template: string, params?: TranslationParams): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in params ? String(params[key]) : match,
  );
}

export function translate(dict: Dictionary, key: TranslationKey, params?: TranslationParams): string {
  return interpolate(resolvePath(dict, key), params);
}
