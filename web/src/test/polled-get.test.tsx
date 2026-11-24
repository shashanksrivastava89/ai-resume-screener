/** A poll that has not changed should not cost a payload or a re-render.
 *
 *  The server has already done the work by the time it answers - after the
 *  2026-09-10 pass that is a few milliseconds - so what a validator saves is the
 *  body, and the client parsing and re-rendering something identical to what it
 *  already holds. It must fall back cleanly on a server that sends no validator.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { getPolled } from "../api";

function reply(status: number, body: unknown, etag?: string) {
  return {
    ok: status < 400,
    status,
    headers: new Headers({
      ...(etag ? { ETag: etag } : {}),
      "content-type": "application/json",
    }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

afterEach(() => vi.restoreAllMocks());

describe("a polled GET", () => {
  it("sends the validator it was given, and reuses the value on 304", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(reply(200, { n: 1 }, '"abc"'))
      .mockResolvedValueOnce(reply(304, null, '"abc"'));
    vi.stubGlobal("fetch", fetchMock);

    expect(await getPolled("/api/notifications")).toEqual({ n: 1 });
    expect(await getPolled("/api/notifications")).toEqual({ n: 1 });

    const secondHeaders = fetchMock.mock.calls[1][1].headers;
    expect(secondHeaders["If-None-Match"]).toBe('"abc"');
  });

  it("takes the new value when the answer moved", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(reply(200, { n: 1 }, '"abc"'))
      .mockResolvedValueOnce(reply(200, { n: 2 }, '"def"')));

    expect(await getPolled("/api/screen/active")).toEqual({ n: 1 });
    expect(await getPolled("/api/screen/active")).toEqual({ n: 2 });
  });

  it("works against a server that sends no validator at all", async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(200, { n: 7 }));
    vi.stubGlobal("fetch", fetchMock);

    expect(await getPolled("/api/plain")).toEqual({ n: 7 });
    expect(await getPolled("/api/plain")).toEqual({ n: 7 });
    expect(fetchMock.mock.calls[1][1].headers["If-None-Match"]).toBeUndefined();
  });
});
