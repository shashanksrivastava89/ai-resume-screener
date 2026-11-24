import { useCallback, useEffect, useState } from "react";
import { get } from "./api";
import type { FeatureFlag, ScoringConfig } from "./types";

interface FetchState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

// A tiny stale-while-revalidate layer: serve the last-good response for a path
// instantly (so tab revisits don't blank), then revalidate in the background;
// and dedupe concurrent requests for the same path (Review + Shortlist both hit
// /review; /api/features is read by several pages). Mutations call invalidate()
// so the next read is fresh.
const _cache = new Map<string, unknown>();
const _inflight = new Map<string, Promise<unknown>>();

// Paths whose value arrived in the bootstrap payload, and how long a mount may use
// it WITHOUT revalidating. Only seeded paths carry this, so every other read keeps
// revalidating on mount exactly as before — the shell's four slow-changing reads are
// the ones being spared, not the page's own data. invalidate() clears it, so a
// mutation still forces a refetch.
const _freshUntil = new Map<string, number>();
const SEED_FRESH_MS = 30_000;

/** Put a value the server already sent into the cache, so the component that would
 *  have fetched it renders from it instead. */
export function seed(path: string, value: unknown): void {
  if (value === undefined) return;
  _cache.set(path, value);
  _freshUntil.set(path, Date.now() + SEED_FRESH_MS);
}

/** Replace `prev` with `next` only if it actually differs.
 *
 *  A poller that calls setState on every tick re-renders its list forever, even
 *  though the answer is almost always identical — the drawer's comment list rebuilt
 *  itself every 6 seconds for as long as it was open (2026-09-10). Passed to
 *  setState as an updater so the comparison sees the CURRENT value, not one captured
 *  in a stale closure. Cheap: these payloads are a few hundred bytes.
 */
export function keepIfSame<T>(next: T): (prev: T) => T {
  return (prev: T) => {
    try {
      return JSON.stringify(prev) === JSON.stringify(next) ? prev : next;
    } catch {
      return next;   // unserialisable (cycles): take the new value
    }
  };
}

/** A seeded value that is still fresh, for readers that do not go through useFetch
 *  (a poller, a component that fetches once on mount). Returns undefined when there
 *  is nothing fresh to use, and the caller fetches as it always did.
 *
 *  It is consumed: the freshness is dropped on the way out, so this answers the FIRST
 *  ask and never a later one. A poller that kept being handed the same seeded value
 *  would not notice a run starting until the window expired — which is precisely the
 *  thing it exists to notice. */
export function takeFresh<T>(path: string): T | undefined {
  if (!isFresh(path)) return undefined;
  _freshUntil.delete(path);
  return _cache.get(path) as T | undefined;
}

function isFresh(path: string): boolean {
  const until = _freshUntil.get(path);
  return until !== undefined && Date.now() < until;
}

/** Drop cached entries whose path starts with `prefix` (default: everything).
 *  Call after a mutation so the affected reads refetch. */
export function invalidate(prefix = ""): void {
  for (const k of Array.from(_cache.keys())) if (k.startsWith(prefix)) _cache.delete(k);
  for (const k of Array.from(_inflight.keys())) if (k.startsWith(prefix)) _inflight.delete(k);
  for (const k of Array.from(_freshUntil.keys())) if (k.startsWith(prefix)) _freshUntil.delete(k);
}

function fetchShared<T>(path: string): Promise<T> {
  const existing = _inflight.get(path);
  if (existing) return existing as Promise<T>;
  const p = get<T>(path)
    .then((d) => { _cache.set(path, d); return d; })
    .finally(() => { _inflight.delete(path); });
  _inflight.set(path, p);
  return p;
}

// GET a path into {data, error, loading} with a manual reload(). Re-fetches when
// `path` changes (null path = skip, e.g. before a role is chosen). Cached data
// shows immediately while a background revalidation runs.
export function useFetch<T>(path: string | null): FetchState<T> {
  const cached = path ? (_cache.get(path) as T | undefined) : undefined;
  const [data, setData] = useState<T | null>(cached ?? null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path && cached === undefined);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => {
    if (path) _freshUntil.delete(path);   // an explicit reload always goes to the server
    setTick((t) => t + 1);
  }, [path]);

  useEffect(() => {
    if (!path) {
      setData(null);
      setLoading(false);
      return;
    }
    let live = true;
    const hit = _cache.get(path) as T | undefined;
    if (hit !== undefined) { setData(hit); setLoading(false); }
    else setLoading(true);
    setError(null);
    // A value the shell just received from /api/bootstrap does not need fetching
    // again the moment a component mounts to read it. Without this the seed saves
    // nothing: stale-while-revalidate would fire all four requests anyway.
    if (hit !== undefined && isFresh(path)) return () => { live = false; };
    fetchShared<T>(path)
      .then((d) => live && (setData(d), setLoading(false)))
      .catch((e) => live && (setError(e.message || String(e)), setLoading(false)));
    return () => {
      live = false;
    };
  }, [path, tick]);

  return { data, error, loading, reload };
}

/** Run `tick` every `ms`, but never while the tab is hidden — and once immediately
 *  when it comes back.
 *
 *  A poll that fires in a background tab costs the server exactly as much as one
 *  someone is watching, and buys nothing: whatever it fetched is re-fetched on the
 *  way back anyway. Measured on the hosted app (2026-09-10), 55% of ALL requests
 *  were two loops that never stopped — the drawer's comments every 6s and the
 *  notifications bell every 30s — most of them into tabs nobody had in front of
 *  them. `usePolling` below already worked this way; these two predate it. */
export function useHiddenAwarePoll(
  tick: () => void | Promise<void>, ms: number, deps: unknown[],
): void {
  useEffect(() => {
    let live = true;
    let timer = 0;
    const hidden = () => typeof document !== "undefined" && document.hidden;
    const run = async () => {
      if (!live) return;
      if (!hidden()) { try { await tick(); } catch { /* transient */ } }
      if (live) timer = window.setTimeout(run, ms);
    };
    timer = window.setTimeout(run, ms);
    const onVisible = () => { if (!hidden()) void run(); };
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisible);
    }
    return () => {
      live = false;
      window.clearTimeout(timer);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisible);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

// --- Polling that knows when to hurry (2026-09-05) ------------------------------
//
// Two loops polled the server every 2 seconds for as long as a page was open:
// the dashboard for any in-flight screening, the workspace for this requisition's.
// Almost always nothing was running, and each poll re-authorised the requisition
// on the server (~200 filesystem round trips on the hosted share, before the
// backend fix) — background load that starved the clicks the recruiter was
// actually waiting on, on a single vCPU.
//
// The contract now: FAST (2 s) only while something is live or right after the
// user starts something, SLOW otherwise, and PAUSED while the tab is hidden —
// with an immediate tick the moment it becomes visible again, so nothing ever
// looks stale to a person who is looking at it.

const POLL_FAST_MS = 2000;
const POLL_SLOW_MS = 20000;
const HURRY_WINDOW_MS = 30000;   // after bumpPolling(): stay fast this long
const HURRY_EVENT = "screener:poll-now";
let hurryUntil = 0;

/** Call when the user starts something the pollers should notice at once (a
 *  screening run). Every active poller ticks immediately and stays fast for a
 *  while, so a banner never waits 20 s for the next slow tick. */
export function bumpPolling(): void {
  hurryUntil = Date.now() + HURRY_WINDOW_MS;
  if (typeof window !== "undefined") window.dispatchEvent(new Event(HURRY_EVENT));
}

/** Run `tick` on an adaptive cadence. `tick` returns true while there is live work
 *  worth watching closely (a running job); false lets the loop slow down. */
export function usePolling(tick: () => Promise<boolean>, enabled: boolean, deps: unknown[] = []): void {
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    let timer: number | undefined;
    let running = false;
    const schedule = (busy: boolean) => {
      if (!live) return;
      if (timer) window.clearTimeout(timer);
      if (typeof document !== "undefined" && document.hidden) return; // resumes on visibility
      const fast = busy || Date.now() < hurryUntil;
      timer = window.setTimeout(run, fast ? POLL_FAST_MS : POLL_SLOW_MS);
    };
    const run = async () => {
      if (!live || running) return;
      running = true;
      let busy = false;
      try { busy = await tick(); } catch { /* transient — keep polling */ }
      running = false;
      schedule(busy);
    };
    const onVisible = () => { if (!document.hidden) void run(); };
    const onHurry = () => { void run(); };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener(HURRY_EVENT, onHurry);
    void run();
    return () => {
      live = false;
      if (timer) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener(HURRY_EVENT, onHurry);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps]);
}

// Resolve one feature flag's global on/off (P42). The hm_portal flag that
// dark-launches the HM collaboration is env-controlled, so `enabled` is the
// authoritative value; for file/db flags we fall back to the default. Region
// overrides aren't consulted here — env flags don't carry them. Returns
// undefined while /api/features is still loading so callers can hold rendering.
export function useFeatureFlag(key: string): boolean | undefined {
  const { data } = useFetch<{ features: FeatureFlag[] }>("/api/features");
  if (!data) return undefined;
  const f = data.features.find((x) => x.key === key);
  if (!f) return false;
  return f.control === "env" ? f.enabled : f.default;
}

// P51: the published scoring values + resolved display strings — the single
// source for every on-screen scoring fact (never a literal in JSX, so the copy
// can't rot when the model changes). Publishes call invalidate("/api/scoring-config").
export function useScoringConfig(): ScoringConfig | null {
  const { data } = useFetch<ScoringConfig>("/api/scoring-config");
  return data;
}
