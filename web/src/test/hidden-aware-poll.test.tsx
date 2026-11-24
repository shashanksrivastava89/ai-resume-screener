/** A poll that fires into a background tab costs the server exactly as much as one
 *  someone is watching, and buys nothing — whatever it fetched is re-fetched on the
 *  way back. Measured hosted (2026-09-10): 55% of ALL requests were the drawer's
 *  comment poll and the notifications bell, neither of which stopped when hidden. */
import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useHiddenAwarePoll } from "../hooks";

function hide(hidden: boolean) {
  Object.defineProperty(document, "hidden", { value: hidden, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}

function Poller({ tick, ms }: { tick: () => void; ms: number }) {
  useHiddenAwarePoll(tick, ms, []);
  return null;
}

afterEach(() => { vi.useRealTimers(); hide(false); });

describe("polling while nobody is looking", () => {
  it("keeps polling a visible tab", async () => {
    vi.useFakeTimers();
    const tick = vi.fn();
    render(<Poller tick={tick} ms={100} />);
    await vi.advanceTimersByTimeAsync(350);
    expect(tick.mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it("stops firing once the tab is hidden", async () => {
    vi.useFakeTimers();
    const tick = vi.fn();
    render(<Poller tick={tick} ms={100} />);
    await vi.advanceTimersByTimeAsync(250);
    const before = tick.mock.calls.length;
    expect(before).toBeGreaterThan(0);

    hide(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(tick.mock.calls.length).toBe(before);
  });

  it("catches up immediately when the tab comes back", async () => {
    vi.useFakeTimers();
    const tick = vi.fn();
    render(<Poller tick={tick} ms={10_000} />);
    hide(true);
    await vi.advanceTimersByTimeAsync(100);
    tick.mockClear();

    hide(false);
    await vi.waitFor(() => expect(tick).toHaveBeenCalled());
  });

  it("survives a failing tick and keeps polling", async () => {
    vi.useFakeTimers();
    const tick = vi.fn().mockRejectedValue(new Error("network"));
    render(<Poller tick={tick} ms={100} />);
    await vi.advanceTimersByTimeAsync(350);
    expect(tick.mock.calls.length).toBeGreaterThanOrEqual(3);
  });
});
