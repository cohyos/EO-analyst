import { useEffect, useRef, useState } from "react";

/**
 * Minimal fixed-row-height windowing for a dense feed list — no external
 * virtualization library, just enough to avoid mounting hundreds of DOM
 * rows when the feed grows. Rows are assumed to have identical height.
 */
export function useVirtualList<T>({
  items,
  rowHeight,
  overscan = 6,
}: {
  items: T[];
  rowHeight: number;
  overscan?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(600);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onScroll = () => setScrollTop(el.scrollTop);
    const ro = new ResizeObserver(() => setViewportHeight(el.clientHeight));
    el.addEventListener("scroll", onScroll, { passive: true });
    ro.observe(el);
    setViewportHeight(el.clientHeight);
    return () => {
      el.removeEventListener("scroll", onScroll);
      ro.disconnect();
    };
  }, []);

  const totalHeight = items.length * rowHeight;
  const startIndex = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const endIndex = Math.min(
    items.length,
    Math.ceil((scrollTop + viewportHeight) / rowHeight) + overscan,
  );
  const visibleItems = items.slice(startIndex, endIndex).map((item, i) => ({
    item,
    index: startIndex + i,
    top: (startIndex + i) * rowHeight,
  }));

  function scrollToIndex(index: number) {
    const el = containerRef.current;
    if (!el) return;
    const top = index * rowHeight;
    if (top < el.scrollTop) el.scrollTop = top;
    else if (top + rowHeight > el.scrollTop + el.clientHeight) {
      el.scrollTop = top + rowHeight - el.clientHeight;
    }
  }

  return { containerRef, totalHeight, visibleItems, scrollToIndex };
}
