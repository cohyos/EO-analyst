import { useEffect, useRef, useState, type CSSProperties, type RefObject } from "react";

/**
 * Round-3 mobile fix (UI-MOBILE-iphone-r3.md #2): several filter popovers (`ProductLineFilter`,
 * `FeedFilters`' country menu) are `position: absolute` inside a `relative` wrapper no wider than
 * their own trigger button, anchored with `insetInlineStart: 0` and a fixed width -- on a narrow
 * (390px) screen a trigger sitting close to one edge of the viewport left the popover clipped by
 * that edge, with no way to reach the options past the fold.
 *
 * Measures the trigger's bounding rect once the popover opens and, when its physical left edge
 * sits too close to the viewport's left edge (< 8px clearance) for the popover to fit growing
 * further left, flips the anchor to the opposite (`insetInlineEnd`) side instead so it grows into
 * the open space on the right. Combine with a `max-w-[calc(100vw-2rem)]` on the popover itself so
 * it never exceeds the viewport regardless of which side it anchors from.
 */
export function usePopoverEdgeClamp<T extends HTMLElement = HTMLButtonElement>(
  open: boolean,
): {
  triggerRef: RefObject<T | null>;
  popoverStyle: CSSProperties;
} {
  const triggerRef = useRef<T | null>(null);
  const [flip, setFlip] = useState(false);

  useEffect(() => {
    if (!open) {
      setFlip(false);
      return;
    }
    const el = triggerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setFlip(rect.left < 8);
  }, [open]);

  return {
    triggerRef,
    popoverStyle: flip ? { insetInlineEnd: 0 } : { insetInlineStart: 0 },
  };
}
