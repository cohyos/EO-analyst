import { describe, expect, it } from "vitest";
import { domainLabel, domainSubdomainLabel, subdomainLabel } from "./taxonomy";

describe("domainLabel", () => {
  it("maps a known domain id to its Hebrew label", () => {
    expect(domainLabel("air_defense")).toBe('הגנה אווירית ויירוט');
  });

  it("falls back to the raw id for an unknown domain, and to an em dash for none", () => {
    expect(domainLabel("some_future_domain")).toBe("some_future_domain");
    expect(domainLabel(null)).toBe("—");
    expect(domainLabel(undefined)).toBe("—");
  });
});

// Content review (docs/qa/content_review/CR-ui.md): subdomain ids (config/taxonomy.yaml
// `domains.*.sub`) showed up as raw untranslated slugs outside the feed's own filter chips --
// e.g. the patents table's "image_processing", "droic_digital_pixel", "cv_atr".
describe("subdomainLabel", () => {
  it("translates a known subdomain id from any domain", () => {
    expect(subdomainLabel("image_processing")).toContain("עיבוד תמונה");
    expect(subdomainLabel("droic_digital_pixel")).toContain("FPA");
    expect(subdomainLabel("border_towers")).toContain("תצפית גבולות");
  });

  it("falls back to the raw id for an unmapped subdomain", () => {
    expect(subdomainLabel("some_future_subdomain")).toBe("some_future_subdomain");
  });

  it("returns an em dash for a missing subdomain", () => {
    expect(subdomainLabel(null)).toBe("—");
    expect(subdomainLabel(undefined)).toBe("—");
    expect(subdomainLabel("")).toBe("—");
  });
});

// Content review: `ProductLine.subdomains` (`GET /api/product-lines/:id`) is a composite
// `"<domain>.<subdomain>"` path -- the product-line detail page's header used to render the
// whole raw path (e.g. `airborne_pods.targeting_pods`) as-is right below the page title.
describe("domainSubdomainLabel", () => {
  it("translates the subdomain half of a dot-separated composite path", () => {
    expect(domainSubdomainLabel("airborne_pods.targeting_pods")).toBe(
      "פודי ציון מטרות (Targeting Pods)",
    );
  });

  it("translates the subdomain half of a slash-separated composite path", () => {
    expect(domainSubdomainLabel("airborne_pods/targeting_pods")).toBe(
      "פודי ציון מטרות (Targeting Pods)",
    );
  });

  it("falls back to plain subdomainLabel behavior for a bare subdomain id with no domain prefix", () => {
    expect(domainSubdomainLabel("targeting_pods")).toBe("פודי ציון מטרות (Targeting Pods)");
    expect(domainSubdomainLabel("some_future_subdomain")).toBe("some_future_subdomain");
  });

  it("returns an em dash for a missing path", () => {
    expect(domainSubdomainLabel(null)).toBe("—");
    expect(domainSubdomainLabel(undefined)).toBe("—");
  });
});
