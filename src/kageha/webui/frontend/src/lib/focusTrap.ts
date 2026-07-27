import { useEffect, type RefObject } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

export function getFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
  ).filter((el) => {
    if (el.getAttribute("aria-hidden") === "true") return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    return true;
  });
}

export function createFocusTrap(
  container: HTMLElement,
  opts?: { initialFocus?: HTMLElement | null },
): () => void {
  const previouslyFocused =
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;

  const focusInitial = () => {
    const target =
      opts?.initialFocus || getFocusableElements(container)[0] || container;
    if (typeof target.focus === "function") {
      target.focus();
    }
  };

  // Defer so drawers that just mounted can settle.
  requestAnimationFrame(focusInitial);

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== "Tab") return;
    const nodes = getFocusableElements(container);
    if (!nodes.length) {
      e.preventDefault();
      return;
    }
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    const active = document.activeElement;
    if (e.shiftKey) {
      if (active === first || !container.contains(active)) {
        e.preventDefault();
        last.focus();
      }
    } else if (active === last || !container.contains(active)) {
      e.preventDefault();
      first.focus();
    }
  };

  container.addEventListener("keydown", onKeyDown);
  return () => {
    container.removeEventListener("keydown", onKeyDown);
    if (
      previouslyFocused &&
      typeof previouslyFocused.focus === "function" &&
      document.contains(previouslyFocused)
    ) {
      previouslyFocused.focus();
    }
  };
}

/** Trap focus inside `containerRef` while `active` is true. */
export function useFocusTrap(
  active: boolean,
  containerRef: RefObject<HTMLElement | null>,
  opts?: { initialFocusRef?: RefObject<HTMLElement | null> },
) {
  const initialFocusRef = opts?.initialFocusRef;

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;
    return createFocusTrap(container, {
      initialFocus: initialFocusRef?.current ?? null,
    });
  }, [active, containerRef, initialFocusRef]);
}
