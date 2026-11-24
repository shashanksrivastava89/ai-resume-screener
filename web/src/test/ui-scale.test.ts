/** The load-time display scale (2026-09-11).
 *
 * The pilot kept telling testers to "zoom out to find working space" — a 240px nav plus
 * an 880px candidate drawer does not leave a readable list inside the 1280-CSS-pixel
 * viewport a laptop at 125% Windows scaling hands the page. The app now draws itself
 * slightly smaller when the viewport it gets is tight.
 *
 * The half worth testing is the promise attached to it: once the person reaches for
 * their own browser zoom, the app stops deciding. Zoom moves devicePixelRatio; a window
 * resize does not — that is the whole signal, and it is one-way.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { chooseScale, installUiScale } from "../uiScale";

function viewport(width: number, dpr: number) {
  Object.defineProperty(window, "innerWidth", { value: width, configurable: true, writable: true });
  Object.defineProperty(window, "devicePixelRatio", { value: dpr, configurable: true, writable: true });
}

beforeEach(() => localStorage.clear());

describe("choosing a scale for the viewport we actually got", () => {
  it("leaves a roomy window alone", () => {
    expect(chooseScale(1920)).toBe(1);
    expect(chooseScale(1500)).toBe(1);
  });

  it("gives back a little on the sizes the pilot works at", () => {
    expect(chooseScale(1440)).toBe(0.95);
    // 1920×1080 at 150% Windows scaling — the machine that prompts "please zoom out"
    expect(chooseScale(1280)).toBe(0.9);
    expect(chooseScale(1024)).toBe(0.9);
  });

  it("never goes below 0.9, and never trusts a nonsense width", () => {
    expect(chooseScale(320)).toBe(0.9);   // small, not unreadable
    expect(chooseScale(0)).toBe(1);
    expect(chooseScale(NaN)).toBe(1);
  });
});

describe("it never fights the person", () => {
  it("scales a tight viewport on a first visit", () => {
    viewport(1280, 1);
    expect(installUiScale()).toBe(0.9);
  });

  it("hands the view back for good once they zoom the browser themselves", () => {
    viewport(1280, 1);
    expect(installUiScale()).toBe(0.9);

    // They zoomed out to 80%: the viewport widens AND the device pixel ratio moves,
    // which a window resize would not do.
    viewport(1600, 0.8);
    expect(installUiScale()).toBe(1);

    // And it stays handed back — even back at a width we would have scaled, because
    // the person has said what they want to look at.
    viewport(1280, 0.8);
    expect(installUiScale()).toBe(1);
  });

  it("keeps deciding across ordinary reloads and window resizes", () => {
    viewport(1400, 1);
    expect(installUiScale()).toBe(0.95);
    viewport(1280, 1);            // same zoom, a narrower window
    expect(installUiScale()).toBe(0.9);
  });
});
