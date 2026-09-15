import { useEffect, useRef, useState } from "react";

const IDLE_MS = 800;

/**
 * Round-3 mobile fix (UI-MOBILE-iphone-r3.md #1): the floating "שאל את האנליסט" trigger sits
 * fixed over `<main>` and covers content while the analyst scrolls a long feed/report on a phone.
 * Tracks the scroll direction of the app's `<main>` element (passive listener) so a caller can
 * hide the trigger while scrolling down and bring it back on scroll-up or after a short idle
 * period -- mirrors the common "hide-on-scroll" app-bar pattern.
 *
 * Returns `true` while the trigger should be hidden (actively scrolling down), `false` otherwise
 * (scrolling up, idle, or no scrollable `<main>` found at all -- e.g. in a unit test that renders
 * this component without the AppShell ancestor).
 */
export function useMainScrollDirection(): boolean {
  const [hidden, setHidden] = useState(false);
  const lastScrollTop = useRef(0);
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const main = document.querySelector("main");
    if (!main) return;

    lastScrollTop.current = main.scrollTop;

    function clearIdleTimer() {
      if (idleTimer.current) {
        clearTimeout(idleTimer.current);
        idleTimer.current = null;
      }
    }

    function onScroll() {
      const top = main!.scrollTop;
      const delta = top - lastScrollTop.current;
      lastScrollTop.current = top;

      if (delta > 4) {
        setHidden(true);
      } else if (delta < -4 || top <= 0) {
        setHidden(false);
      }

      clearIdleTimer();
      idleTimer.current = setTimeout(() => setHidden(false), IDLE_MS);
    }

    main.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      main.removeEventListener("scroll", onScroll);
      clearIdleTimer();
    };
  }, []);

  return hidden;
}
