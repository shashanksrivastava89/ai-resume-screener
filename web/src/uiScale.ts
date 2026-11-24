/** How big the app draws itself, decided once at load (2026-09-11).
 *
 * THE PROBLEM. The pilot kept asking testers to "zoom out to find some working space".
 * Not because anyone's browser is misconfigured — the owner's own machine is 1920×1080
 * at 100% with no zoom set anywhere — but because a laptop at 125% or 150% Windows
 * scaling hands the page a 1280- or 1080-CSS-pixel viewport, and a 240px nav plus an
 * 880px candidate drawer does not leave a list worth reading inside that.
 *
 * WHAT THIS DOES. At load it looks at the viewport it actually got and, when that is
 * tight, draws the app slightly smaller so the layout has the room it was designed for.
 * `zoom` goes on the root element, so everything — including the overlays that portal
 * to <body> — scales together and no measurement inside the app changes meaning.
 *
 * THE RULE THAT MATTERS: IT NEVER FIGHTS THE PERSON. A user who reaches for the
 * browser's own zoom has said something about what they want to look at, and the app
 * must not quietly claw it back. Browser zoom moves `devicePixelRatio`, while a window
 * resize does not — so if that ratio ever differs from the one we planned against, the
 * person has taken control: we drop to 1.0 and never scale again on this browser. It is
 * one-way on purpose. Being slightly too big is a preference; being unexpectedly small
 * is the complaint this exists to fix.
 */

const KEY = "ui-scale.v1";

interface Saved {
  /** devicePixelRatio when we last set a scale ourselves. */
  dpr: number;
  /** The person zoomed; leave their view alone from now on. */
  userTook?: boolean;
}

/** The scale for a viewport width — a pure function so it can be reasoned about and
 *  tested. The steps are the app's own breakpoints: the layout was measured to fit a
 *  1280px laptop with the table's columns intact, and the drawer wants ~1400 before the
 *  ranked list beside it is worth having. Nothing below 0.9: smaller than that is not
 *  "more room", it is unreadable. */
export function chooseScale(width: number): number {
  if (!Number.isFinite(width) || width <= 0) return 1;
  if (width >= 1500) return 1;
  if (width >= 1320) return 0.95;
  return 0.9;
}

function read(): Saved | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Saved) : null;
  } catch {
    return null;   // private window, blocked storage — scaling is a nicety, not a need
  }
}

function write(value: Saved): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(value));
  } catch {
    /* nothing to do: we simply decide again next load */
  }
}

function setZoom(scale: number): void {
  const root = document.documentElement;
  if (scale === 1) root.style.removeProperty("zoom");
  else root.style.zoom = String(scale);
}

/** Apply the load-time scale and watch for the person taking over. Returns the scale it
 *  applied — jsdom does not implement `zoom`, so the decision is what a test can hold
 *  onto; the rendering itself was checked in a real browser. */
export function installUiScale(): number {
  if (typeof window === "undefined" || typeof document === "undefined") return 1;
  const saved = read();
  if (saved?.userTook) {
    setZoom(1);
    return 1;
  }
  const dpr = window.devicePixelRatio || 1;
  // A different device pixel ratio than the one we planned against, between loads, is
  // the person's own zoom: hand the view back permanently.
  if (saved && Math.abs(saved.dpr - dpr) > 0.01) {
    write({ dpr, userTook: true });
    setZoom(1);
    return 1;
  }
  const scale = chooseScale(window.innerWidth);
  setZoom(scale);
  write({ dpr });

  // The same handover, live: zoom fires a resize, and only zoom moves the ratio.
  window.addEventListener("resize", () => {
    const now = window.devicePixelRatio || 1;
    if (Math.abs(now - dpr) > 0.01) {
      write({ dpr: now, userTook: true });
      setZoom(1);
    }
  });
  return scale;
}
