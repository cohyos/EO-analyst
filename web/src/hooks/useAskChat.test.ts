import { describe, expect, it } from "vitest";
import { friendlyAskErrorMessage } from "./useAskChat";

describe("friendlyAskErrorMessage", () => {
  it("maps WebKit's bare 'Load failed' TypeError to a clear Hebrew explanation", () => {
    const msg = friendlyAskErrorMessage(new Error("Load failed"));
    expect(msg).toContain("החיבור לשרת נקטע");
    expect(msg).not.toContain("Load failed");
  });

  it("maps WebKit's 'cancelled' network error the same way", () => {
    expect(friendlyAskErrorMessage(new Error("cancelled"))).toContain("החיבור לשרת נקטע");
  });

  it("maps Chromium's 'Failed to fetch' the same way", () => {
    expect(friendlyAskErrorMessage(new Error("Failed to fetch"))).toContain("החיבור לשרת נקטע");
  });

  it("maps Firefox's NetworkError the same way", () => {
    expect(
      friendlyAskErrorMessage(new Error("NetworkError when attempting to fetch resource.")),
    ).toContain("החיבור לשרת נקטע");
  });

  it("is case-insensitive", () => {
    expect(friendlyAskErrorMessage(new Error("LOAD FAILED"))).toContain("החיבור לשרת נקטע");
  });

  it("passes through a specific server error message unchanged", () => {
    expect(friendlyAskErrorMessage(new Error("שגיאת שרת (500)"))).toBe("שגיאת שרת (500)");
  });

  it("falls back to the generic Hebrew message on an empty message", () => {
    expect(friendlyAskErrorMessage(new Error(""))).toBe("שגיאה בתקשורת עם השרת");
  });
});
