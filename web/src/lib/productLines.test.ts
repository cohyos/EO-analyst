import { describe, expect, it } from "vitest";
import { PRODUCT_LINE_CATALOG, productLineLabel, productLineOption } from "./productLines";

describe("productLines catalog (PL-ui)", () => {
  it("has exactly the six frozen product-line ids from the task brief", () => {
    expect(PRODUCT_LINE_CATALOG.map((p) => p.id)).toEqual([
      "targeting_pods",
      "mws_eo",
      "lorop_pods",
      "eo_air_defense_warning",
      "ball_gimbals_16in",
      "border_long_range_eo",
    ]);
  });

  it("resolves a known id to its Hebrew/English names", () => {
    const opt = productLineOption("targeting_pods");
    expect(opt.nameHe).toBe("פודי ציון מטרות / תקיפה");
    expect(opt.nameEn).toBe("Targeting Pods");
  });

  it("falls back to the raw id for an unknown product line rather than throwing", () => {
    const opt = productLineOption("unknown_future_line");
    expect(opt.id).toBe("unknown_future_line");
    expect(opt.nameHe).toBe("unknown_future_line");
    expect(opt.nameEn).toBe("unknown_future_line");
  });

  it("productLineLabel resolves per locale", () => {
    expect(productLineLabel("mws_eo", "en")).toBe("EO-based Missile Warning Systems (MWS)");
    expect(productLineLabel("mws_eo", "he")).toBe("מערכות התראה להגנה עצמית מבוססות EO (MWS)");
  });
});
