// P38: the region-lens picker. Hidden when the region framework is unseeded
// (me.regions empty) so local dev is unchanged; when regions are present it lists
// "All regions" + the codes, persists the choice to localStorage, and the
// Requisitions list sends it as ?region= on its fetch.
import { describe, it, expect, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RegionPicker from "../components/RegionPicker";
import Requisitions from "../pages/Requisitions";
import { RegionLensProvider } from "../RegionContext";
import { ToastProvider } from "../components/overlays";
import { mockApi, renderPage } from "./utils";
import type { Me, RoleCard } from "../types";

function meWith(regions?: { all: boolean; codes: string[] }): Me {
  return {
    user: "dev@example.com", role: "TASpecialist", roles: ["TASpecialist"], known_role: true,
    capabilities: ["recruiter"], has_admin: false, can_edit_rules: false,
    evaluation_mode: true, review_pending: false, last_review: null, regions,
  };
}

function card(req_id: string, region: string): RoleCard {
  return {
    req_id, title: req_id, job_location: region, location_display: region, law: "—",
    stage: "screened", stage_label: "Screened", progress: "", batch_count: 0,
    screened: true, reviewed_count: 0, candidate_count: 3, rubric_version: 2,
    cta: { label: "Review", goto: "review", magic: false },
    req_code: "REQ-1", lifecycle: { status: "active", outcome: null, closed_at: null },
    last_activity_utc: null, suggest_close: { suggest: false, decided: 0, quiet_days: 0 },
    jd_language: null, hm: null, created_by: "dev@example.com", region, tz: "UTC",
  } as unknown as RoleCard;
}

const ROLES = {
  metrics: { active_roles: 1, candidates_screened: 3, awaiting_review: 1, total: 1,
    need_action: 1, awaiting_hm: 0, suggested_close: 0, history_count: 0 },
  roles: [card("apac-role", "APAC")],
};

beforeEach(() => {
  try { localStorage.clear(); } catch { /* jsdom */ }
});

describe("RegionPicker", () => {
  it("is hidden when the region framework is unseeded (no regions)", async () => {
    const api = mockApi({});
    renderPage(
      <RegionLensProvider><RegionPicker /></RegionLensProvider>,
      { me: meWith(undefined) }
    );
    await waitFor(() => expect(api.callsTo("/api/me").length).toBeGreaterThan(0));
    expect(screen.queryByLabelText("Region lens")).toBeNull();
  });

  it("lists All regions + the codes and persists the chosen lens", async () => {
    mockApi({});
    renderPage(
      <RegionLensProvider><RegionPicker /></RegionLensProvider>,
      { me: meWith({ all: false, codes: ["IN", "APAC"] }) }
    );
    const trigger = await screen.findByLabelText("Region lens");
    expect(trigger).toHaveTextContent("All regions");

    await userEvent.click(trigger);
    // menu offers All + each accessible region
    expect(screen.getByRole("menuitem", { name: "All regions" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "APAC" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("menuitem", { name: "APAC" }));
    expect(trigger).toHaveTextContent("APAC");
    expect(localStorage.getItem("rs.regionLens")).toBe("APAC");
  });
});

describe("Requisitions region lens", () => {
  it("sends ?region= from the persisted lens on the roles fetch", async () => {
    localStorage.setItem("rs.regionLens", "APAC");
    const api = mockApi({ "/api/roles": ROLES, "/api/features": { features: [] } });
    renderPage(
      <RegionLensProvider><Requisitions /></RegionLensProvider>,
      { route: "/reqs", me: meWith({ all: false, codes: ["APAC"] }) }
    );
    await screen.findByText("apac-role");
    const call = api.callsTo("/api/roles").find((c) => c.method === "GET");
    expect(call?.url).toContain("region=APAC");
  });

  it("fetches plain /api/roles when no lens is set", async () => {
    const api = mockApi({ "/api/roles": ROLES, "/api/features": { features: [] } });
    renderPage(
      <RegionLensProvider><Requisitions /></RegionLensProvider>,
      { route: "/reqs", me: meWith({ all: true, codes: ["IN", "APAC"] }) }
    );
    await screen.findByText("apac-role");
    const call = api.callsTo("/api/roles").find((c) => c.method === "GET");
    expect(call?.url).not.toContain("region=");
  });
});

// --- where the lens OPENS, and what switching it means (2026-09-12) -----------------

describe("the lens opens somewhere", () => {
  function meHome(codes: string[], home: string | null,
                  home_source: string | null = home ? "country" : null): Me {
    return {
      ...meWith({ all: false, codes }),
      regions: {
        all: false, codes, home, home_source,
        names: Object.fromEntries(codes.map((c) => [c, c === "EMEA" ? "Europe, Middle East & Africa" : c])),
      },
    } as Me;
  }

  it("opens on the person's default region with nothing stored", async () => {
    const api = mockApi({ "/api/roles": ROLES, "/api/features": { features: [] } });
    renderPage(
      <RegionLensProvider><Requisitions /></RegionLensProvider>,
      { route: "/reqs", me: meHome(["EMEA", "AMER"], "EMEA") }
    );
    await screen.findByText("apac-role");
    const calls = api.callsTo("/api/roles").filter((c) => c.method === "GET");
    // the FIRST fetch already carries it — no unfiltered load, then a refetch
    expect(calls[0]?.url).toContain("region=EMEA");
  });

  it("an explicit All regions beats the default and is remembered as a choice", async () => {
    localStorage.setItem("rs.regionLens", "all");
    const api = mockApi({ "/api/roles": ROLES, "/api/features": { features: [] } });
    renderPage(
      <RegionLensProvider><Requisitions /></RegionLensProvider>,
      { route: "/reqs", me: meHome(["EMEA", "AMER"], "EMEA") }
    );
    await screen.findByText("apac-role");
    const call = api.callsTo("/api/roles").find((c) => c.method === "GET");
    expect(call?.url).not.toContain("region=");
  });

  it("a stored region the person can no longer reach falls back to everything", async () => {
    localStorage.setItem("rs.regionLens", "AMER");
    const api = mockApi({ "/api/roles": ROLES, "/api/features": { features: [] } });
    renderPage(
      <RegionLensProvider><Requisitions /></RegionLensProvider>,
      { route: "/reqs", me: meHome(["EMEA"], "EMEA") }
    );
    await screen.findByText("apac-role");
    const call = api.callsTo("/api/roles").find((c) => c.method === "GET");
    expect(call?.url).not.toContain("region=AMER");
  });

  it("names regions from the server and offers to set a default", async () => {
    const api = mockApi({ "/api/me/home-region": { ok: true } });
    renderPage(
      <ToastProvider><RegionLensProvider><RegionPicker /></RegionLensProvider></ToastProvider>,
      { me: meHome(["EMEA", "AMER"], null, null) }
    );
    const trigger = await screen.findByLabelText("Region lens");
    await userEvent.click(trigger);
    // the name comes from me.regions.names, not from a copy in this file
    await userEvent.click(screen.getByRole("menuitem", { name: /Europe, Middle East & Africa/ }));

    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole("menuitem", { name: /Make .* my default/ }));
    await waitFor(() => expect(api.callsTo("/api/me/home-region")).toHaveLength(1));
    expect(api.callsTo("/api/me/home-region")[0].body).toEqual({ region: "EMEA" });
  });

  it("says what switching region changes, and what it never changes", async () => {
    mockApi({});
    renderPage(
      <ToastProvider><RegionLensProvider><RegionPicker /></RegionLensProvider></ToastProvider>,
      { me: meHome(["EMEA", "AMER"], "EMEA") }
    );
    const dot = await screen.findByLabelText("What switching region changes");
    await userEvent.hover(dot);
    // the two claims that matter: the law follows the requisition, not the lens
    expect(await screen.findByText(/follow each requisition's own job location/)).toBeInTheDocument();
    expect(screen.getByText(/one scoring model, everywhere/)).toBeInTheDocument();
  });
});
