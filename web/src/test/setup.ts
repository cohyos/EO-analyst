import "@testing-library/jest-dom/vitest";

// jsdom doesn't implement ResizeObserver; useVirtualList relies on it to
// track the feed container's height.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (!("ResizeObserver" in window)) {
  // @ts-expect-error -- test polyfill
  window.ResizeObserver = ResizeObserverStub;
}

if (!("matchMedia" in window)) {
  // @ts-expect-error -- test polyfill
  window.matchMedia = () => ({
    matches: false,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
  });
}
