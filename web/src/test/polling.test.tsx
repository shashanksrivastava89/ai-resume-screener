// Adaptive polling (2026-09-05). Two loops used to hit the server every 2 s for as
// long as a page was open, whether or not anything was running — on a single vCPU
// that background load is what made every click feel heavy. The contract now:
// fast while something is live or was just started, slow otherwise, paused while
// the tab is hidden, and an immediate tick when it matters.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePolling, bumpPolling } from "../hooks";

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { value: hidden, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}

async function flush() {
  // let the awaited tick settle and the next timer be scheduled
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

describe("usePolling", () => {
  beforeEach(() => { vi.useFakeTimers(); setHidden(false); });
  afterEach(() => { vi.useRealTimers(); });

  it("polls fast while the tick reports live work", async () => {
    const tick = vi.fn().mockResolvedValue(true);
    renderHook(() => usePolling(tick, true));
    await flush();
    expect(tick).toHaveBeenCalledTimes(1);          // immediately on mount
    await act(async () => { vi.advanceTimersByTime(2000); });
    await flush();
    expect(tick).toHaveBeenCalledTimes(2);          // 2 s later — fast cadence
  });

  it("slows right down when nothing is running", async () => {
    const tick = vi.fn().mockResolvedValue(false);
    renderHook(() => usePolling(tick, true));
    await flush();
    expect(tick).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(2000); });
    await flush();
    expect(tick).toHaveBeenCalledTimes(1);          // NOT at 2 s any more
    await act(async () => { vi.advanceTimersByTime(18000); });
    await flush();
    expect(tick).toHaveBeenCalledTimes(2);          // ...but at 20 s
  });

  it("hurries when the user starts something, and ticks at once", async () => {
    const tick = vi.fn().mockResolvedValue(false);
    renderHook(() => usePolling(tick, true));
    await flush();
    expect(tick).toHaveBeenCalledTimes(1);
    act(() => { bumpPolling(); });                    // e.g. Screen résumés clicked
    await flush();
    expect(tick).toHaveBeenCalledTimes(2);          // immediate
    await act(async () => { vi.advanceTimersByTime(2000); });
    await flush();
    expect(tick).toHaveBeenCalledTimes(3);          // and fast for a while
  });

  it("pauses while the tab is hidden and ticks the moment it is visible again", async () => {
    const tick = vi.fn().mockResolvedValue(true);
    renderHook(() => usePolling(tick, true));
    await flush();
    act(() => { setHidden(true); });
    await act(async () => { vi.advanceTimersByTime(2000); });   // the pending fast tick fires…
    await flush();
    const whileHidden = tick.mock.calls.length;
    await act(async () => { vi.advanceTimersByTime(60000); });  // …but schedules nothing further
    await flush();
    expect(tick).toHaveBeenCalledTimes(whileHidden);
    act(() => { setHidden(false); });
    await flush();
    expect(tick).toHaveBeenCalledTimes(whileHidden + 1);        // instant catch-up
  });

  it("does nothing at all when disabled (a non-recruiter never polls a 403)", async () => {
    const tick = vi.fn().mockResolvedValue(true);
    renderHook(() => usePolling(tick, false));
    await flush();
    await act(async () => { vi.advanceTimersByTime(30000); });
    expect(tick).not.toHaveBeenCalled();
  });

  it("keeps polling through a failed tick", async () => {
    const tick = vi.fn().mockRejectedValueOnce(new Error("network")).mockResolvedValue(true);
    renderHook(() => usePolling(tick, true));
    await flush();
    await act(async () => { vi.advanceTimersByTime(20000); });  // a failure schedules SLOW
    await flush();
    expect(tick).toHaveBeenCalledTimes(2);
  });
});
