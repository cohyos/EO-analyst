import { test as base, expect } from "@playwright/test";
import { attachConsoleErrorCollector } from "../utils/helpers";

export interface ConsoleErrorCollector {
  getErrors: () => string[];
  clear: () => void;
}

export const test = base.extend<{ consoleErrors: ConsoleErrorCollector }>({
  // Attach BEFORE any navigation happens in the test body, so it catches
  // errors from the very first page load, not just later interactions.
  consoleErrors: async ({ page }, use) => {
    const collector = attachConsoleErrorCollector(page);
    await use(collector);
  },
});

export { expect };
