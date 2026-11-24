/** One request for the whole shell.
 *
 *  Opening any page fired seven, in two dependent waves — identity, then a burst of
 *  five, then one that had waited for the burst. Each wave is a round trip to India
 *  before anything renders, and the later ones queue behind the earlier ones on the
 *  single worker (2026-09-10). /api/bootstrap carries all of it; these pin that the
 *  seed is actually USED, and that it is used exactly once.
 */
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { invalidate, seed, takeFresh, useFetch } from "../hooks";
import * as api from "../api";

afterEach(() => { invalidate(); vi.restoreAllMocks(); });

describe("the bootstrap seed", () => {
  it("spares the fetch a component would have made", async () => {
    const spy = vi.spyOn(api, "get");
    seed("/api/features", { features: [{ key: "hm_portal" }] });

    const { result } = renderHook(() => useFetch<{ features: unknown[] }>("/api/features"));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    expect(result.current.data!.features).toHaveLength(1);
    expect(spy).not.toHaveBeenCalled();
  });

  it("is consumed, so a poller still reaches the server on its next tick", () => {
    seed("/api/screen/active", { jobs: [] });
    expect(takeFresh("/api/screen/active")).toEqual({ jobs: [] });
    expect(takeFresh("/api/screen/active")).toBeUndefined();
  });

  it("never answers for a path nobody seeded", () => {
    expect(takeFresh("/api/roles")).toBeUndefined();
  });

  it("is dropped by a mutation, so the next read is fresh", async () => {
    const spy = vi.spyOn(api, "get").mockResolvedValue({ features: [] } as never);
    seed("/api/features", { features: [{ key: "stale" }] });
    invalidate("/api/features");

    const { result } = renderHook(() => useFetch<{ features: unknown[] }>("/api/features"));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("/api/features"));
    expect(result.current).toBeDefined();
  });

  it("an explicit reload always goes to the server", async () => {
    const spy = vi.spyOn(api, "get").mockResolvedValue({ features: [] } as never);
    seed("/api/features", { features: [{ key: "seeded" }] });

    const { result } = renderHook(() => useFetch<{ features: unknown[] }>("/api/features"));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    expect(spy).not.toHaveBeenCalled();

    result.current.reload();
    await waitFor(() => expect(spy).toHaveBeenCalledWith("/api/features"));
  });
});
