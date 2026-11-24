// The house test pattern every later v2 prompt reuses (Addendum 11 P30):
//
//   mockApi({ "/api/roles": rolesPayload, "/api/roles/x/close": (url, init) => ... })
//   renderPage(<Requisitions />, { route: "/reqs", me: { role: "IT", ... } })
//
// mockApi stubs global.fetch with JSON responses and records every call for
// assertions. renderPage wraps the tree in MemoryRouter + the real MeProvider,
// whose /api/me fetch is served from the same mock (so identity is a plain stub,
// no product code touched). Error responses use apiError(status, detail) — the
// shape api.ts's handle() reads to surface the server's `detail` message.
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";
import { vi } from "vitest";
import { MeProvider } from "../MeContext";
import type { Me } from "../types";

// A route value is either the JSON payload to return (200) or a handler that
// receives (url, init) and returns a payload — or an apiError() for non-2xx.
type RouteHandler = (url: string, init?: RequestInit) => unknown;
export type RouteValue = unknown | RouteHandler;

export interface ApiCall {
  url: string;
  path: string; // url without the query string
  method: string;
  body: unknown; // parsed JSON body when the request carried one
}

// A non-2xx result. mockApi turns it into a response whose json() yields
// { detail } — exactly what api.ts throws on, so error paths are testable.
// detail is unknown so tests can pass structured details (the 409's
// {message, req_id, title}) as well as plain strings.
class MockError {
  constructor(public status: number, public detail: unknown) {}
}

export function apiError(status: number, detail: unknown): MockError {
  return new MockError(status, detail);
}

const state: { routes: Record<string, RouteValue>; calls: ApiCall[] } = {
  routes: {},
  calls: [],
};

export function resetApiMock(): void {
  state.routes = {};
  state.calls = [];
}

// A minimal duck-typed Response with only the surface api.ts's handle() reads
// (ok/status/statusText/headers.get/json/text) — no dependency on a global
// Response implementation, so it behaves identically across Node/jsdom.
function buildResponse(value: unknown, status = 200): Response {
  const isString = typeof value === "string";
  const contentType = isString ? "text/plain" : "application/json";
  const asText = isString ? (value as string) : JSON.stringify(value ?? null);
  const res = {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    headers: { get: (k: string) => (k.toLowerCase() === "content-type" ? contentType : null) },
    json: async () => (isString ? JSON.parse(value as string) : value),
    text: async () => asText,
    // downloadFile() reads .blob(); a real Blob keeps the download path testable.
    blob: async () => new Blob([asText], { type: contentType }),
  };
  return res as unknown as Response;
}

function resolveRoute(url: string, init: RequestInit | undefined): Response {
  const path = url.split("?")[0];
  const route = state.routes[url] ?? state.routes[path];
  if (route === undefined) {
    return buildResponse({ detail: `No mock for ${path}` }, 404);
  }
  const result = typeof route === "function" ? (route as RouteHandler)(url, init) : route;
  if (result instanceof MockError) return buildResponse({ detail: result.detail }, result.status);
  return buildResponse(result);
}

function installFetch(): void {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url =
      typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    let body: unknown;
    if (typeof init?.body === "string") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    }
    state.calls.push({ url, path: url.split("?")[0], method: (init?.method ?? "GET").toUpperCase(), body });
    return resolveRoute(url, init);
  });
  globalThis.fetch = fn as unknown as typeof fetch;
}

export interface ApiMock {
  /** Every fetch the code under test made, in order. */
  readonly calls: ApiCall[];
  /** Calls whose path (query stripped) equals `path`. */
  callsTo(path: string): ApiCall[];
  /** Add or replace one route after setup (e.g. after an action changes state). */
  route(path: string, value: RouteValue): void;
}

export function mockApi(routes: Record<string, RouteValue> = {}): ApiMock {
  Object.assign(state.routes, routes);
  installFetch();
  return {
    get calls() {
      return state.calls;
    },
    callsTo: (path: string) => state.calls.filter((c) => c.path === path),
    route: (path: string, value: RouteValue) => {
      state.routes[path] = value;
    },
  };
}

// A believable default identity (full HR Head). Override per test via the `me`
// option; pass me: null to render the signed-out / no-role state.
export const DEFAULT_ME: Me = {
  user: "dev@example.com",
  role: "HRHead",
  known_role: true,
  capabilities: ["recruiter", "rules_view", "rules_edit", "scoring_edit", "changes_approve", "runs_audit", "data_manage", "verdict_view"],
  has_admin: true,
  can_edit_rules: true,
  evaluation_mode: true,
  review_pending: false,
  last_review: null,
};

export interface RenderPageOptions {
  route?: string;
  me?: Me | null;
}

// Render `ui` inside MemoryRouter (+ the real MeProvider) at `route`. The
// provider's /api/me fetch resolves from the mock: `me` (default DEFAULT_ME) is
// installed unless a mockApi() call already defined /api/me, or me is null (the
// signed-out state — the fetch 404s and MeProvider keeps me = null).
export function renderPage(ui: ReactElement, opts: RenderPageOptions = {}) {
  const { route = "/", me = DEFAULT_ME } = opts;
  if (typeof (globalThis.fetch as { mock?: unknown })?.mock === "undefined") installFetch();
  if (me !== null && state.routes["/api/me"] === undefined) state.routes["/api/me"] = me;
  return render(
    <MemoryRouter initialEntries={[route]}>
      <MeProvider>{ui}</MeProvider>
    </MemoryRouter>
  );
}
