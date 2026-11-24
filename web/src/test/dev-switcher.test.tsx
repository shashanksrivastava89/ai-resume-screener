// The dev user switcher (until SSO/Entra roles are configured): offered only
// when /api/me says dev_switch, persists the chosen identity in localStorage,
// and api.ts rides it on every request as X-Dev-User / X-Dev-Role — so demos
// switch users WITHOUT an app restart (and a live screening run stays visible).
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import AppShell from "../layout/AppShell";
import { get, readDevIdentity, writeDevIdentity } from "../api";
import { mockApi, renderPage, DEFAULT_ME } from "./utils";

function setup(devSwitch: boolean) {
  const api = mockApi({});
  renderPage(
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/reqs" element={<div>home</div>} />
      </Route>
    </Routes>,
    { route: "/reqs", me: { ...DEFAULT_ME, dev_switch: devSwitch } }
  );
  return api;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("Dev user switcher", () => {
  it("is hidden when the server doesn't offer dev switching (SSO active)", async () => {
    setup(false);
    expect(await screen.findByText("home")).toBeInTheDocument();
    expect(screen.queryByText(/More identities…/)).not.toBeInTheDocument();
  });

  it("applies a chosen user + roles to localStorage and reloads", async () => {
    const reload = vi.fn();
    const original = window.location;
    Object.defineProperty(window, "location", {
      value: { ...original, reload },
      writable: true,
    });

    setup(true);
    await userEvent.click(await screen.findByText(/More identities…/));
    const name = screen.getByPlaceholderText("name or email");
    await userEvent.clear(name);
    await userEvent.type(name, "bhavika@example.com");
    await userEvent.click(screen.getByLabelText("TA Specialist"));
    await userEvent.click(screen.getByLabelText("Super Admin"));
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    expect(readDevIdentity()).toEqual({
      user: "bhavika@example.com",
      roles: ["TASpecialist", "SuperAdmin"],
    });
    expect(reload).toHaveBeenCalled();
    Object.defineProperty(window, "location", { value: original, writable: true });
  });

  it("attaches the saved identity to every request as X-Dev headers", async () => {
    mockApi({ "/api/anything": { ok: true } });
    writeDevIdentity({ user: "bhavika@example.com", roles: ["TASpecialist", "SuperAdmin"] });

    await get("/api/anything");

    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    const calls = fetchMock.mock.calls;
    const [, init] = calls[calls.length - 1] as [string, RequestInit];
    expect(init.headers).toMatchObject({
      "X-Dev-User": "bhavika@example.com",
      "X-Dev-Role": "TASpecialist,SuperAdmin",
    });
  });

  it("sends no dev headers when nothing was saved", async () => {
    mockApi({ "/api/anything": { ok: true } });
    await get("/api/anything");
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    const calls = fetchMock.mock.calls;
    const [, init] = calls[calls.length - 1] as [string, RequestInit];
    expect(init.headers ?? {}).not.toHaveProperty("X-Dev-User");
  });
});
