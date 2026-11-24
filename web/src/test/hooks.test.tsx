// Exemplar hook test (Addendum 11 P30) — proves mockApi against the real data
// path: useFetch -> api.get -> global.fetch, including the error contract where
// api.ts surfaces the server's { detail } message.
import { describe, it, expect } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useFetch, invalidate } from "../hooks";
import { apiError, mockApi } from "./utils";

describe("useFetch", () => {
  it("resolves the happy path into { data }", async () => {
    const api = mockApi({ "/api/thing": { hello: "world" } });
    const { result } = renderHook(() => useFetch<{ hello: string }>("/api/thing"));

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ hello: "world" });
    expect(result.current.error).toBeNull();
    expect(api.callsTo("/api/thing")).toHaveLength(1);
  });

  it("surfaces the server's detail message on an error response", async () => {
    mockApi({ "/api/thing": apiError(503, "Azure OpenAI isn't reachable right now.") });
    const { result } = renderHook(() => useFetch<unknown>("/api/thing"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("Azure OpenAI isn't reachable right now.");
    expect(result.current.data).toBeNull();
  });

  it("skips the fetch entirely for a null path", async () => {
    const api = mockApi({});
    const { result } = renderHook(() => useFetch<unknown>(null));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toBeNull();
    expect(api.calls).toHaveLength(0);
  });

  it("re-fetches when reload() is called", async () => {
    const api = mockApi({ "/api/thing": { n: 1 } });
    const { result } = renderHook(() => useFetch<{ n: number }>("/api/thing"));
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }));

    result.current.reload();
    await waitFor(() => expect(api.callsTo("/api/thing")).toHaveLength(2));
  });

  // F9: SWR cache — a second mount serves the cached value immediately (no
  // loading flash) then revalidates in the background.
  it("serves a cached path instantly on remount and still revalidates", async () => {
    const api = mockApi({ "/api/thing": { n: 1 } });
    const first = renderHook(() => useFetch<{ n: number }>("/api/thing"));
    await waitFor(() => expect(first.result.current.data).toEqual({ n: 1 }));

    const second = renderHook(() => useFetch<{ n: number }>("/api/thing"));
    expect(second.result.current.loading).toBe(false); // cache hit — no flash
    expect(second.result.current.data).toEqual({ n: 1 });
    await waitFor(() => expect(api.callsTo("/api/thing")).toHaveLength(2)); // still revalidated
  });

  it("dedupes concurrent requests for the same path into one fetch", async () => {
    const api = mockApi({ "/api/thing": { n: 1 } });
    const a = renderHook(() => useFetch<{ n: number }>("/api/thing"));
    const b = renderHook(() => useFetch<{ n: number }>("/api/thing"));
    await waitFor(() => expect(a.result.current.data).toEqual({ n: 1 }));
    await waitFor(() => expect(b.result.current.data).toEqual({ n: 1 }));
    expect(api.callsTo("/api/thing")).toHaveLength(1); // one shared in-flight request
  });

  it("invalidate() forces the next mount to load fresh", async () => {
    mockApi({ "/api/thing": { n: 1 } });
    const first = renderHook(() => useFetch<{ n: number }>("/api/thing"));
    await waitFor(() => expect(first.result.current.data).toEqual({ n: 1 }));

    invalidate("/api/thing");
    const second = renderHook(() => useFetch<{ n: number }>("/api/thing"));
    expect(second.result.current.loading).toBe(true); // cache dropped → cold load
  });
});
