import type { he } from "./dictionaries/he";

export type Locale = "he" | "en";

/** Recursively widens string-literal leaves to `string` — `he.ts` is declared
 * `as const` (so `DotPaths` below can walk its literal key names), but that
 * would otherwise force every other dictionary's *values* to match Hebrew
 * literally too. */
type Widen<T> = T extends string ? string : { [K in keyof T]: Widen<T[K]> };

/** Canonical dictionary shape, derived from the Hebrew source dictionary. */
export type Dictionary = Widen<typeof he>;

/**
 * Every dot-joined leaf path through the dictionary, e.g. `"nav.feed"` or
 * `"feed.showingStatus"`. Gives `t()` autocomplete + compile-time checking
 * against typos without hand-maintaining a separate key list.
 */
type DotPaths<T, Prefix extends string = ""> = T extends string
  ? Prefix
  : {
      [K in keyof T & string]: DotPaths<T[K], `${Prefix}${Prefix extends "" ? "" : "."}${K}`>;
    }[keyof T & string];

export type TranslationKey = DotPaths<Dictionary>;

export type TranslationParams = Record<string, string | number>;
