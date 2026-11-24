// Who sees which regions (the global rollout, 2026-09-12).
//
// Region scoping was declared and unusable — nothing outside `screener db seed`
// could create a grant. The interesting mistake on this page is NARROWING
// someone, because that is the one that can strand a person, so that is the
// action that has to ask first.
import { describe, it, expect, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Access from "../pages/Access";
import { ToastProvider } from "../components/overlays";
import { mockApi, renderPage, resetApiMock } from "./utils";
import type { Me } from "../types";

const RBAC = {
  caps: [{ key: "recruiter", label: "Screen & review" },
         { key: "access_edit", label: "Grant regions" }],
  rows: [{ name: "SuperAdmin", entra: "SuperAdmin",
           cells: { recruiter: true, access_edit: true } }],
};

const GRANTS = {
  status: "ok",
  message: "",
  regions: [{ code: "EMEA", name: "Europe, Middle East & Africa" },
            { code: "AMER", name: "Americas" }],
  people: [
    { email: "bhavika@example.com", name: "Bhavika Lillis", job_title: "", photo: null,
      initials: "PN", hue: "blue", source: "sso",
      regions: ["EMEA"], region_names: { EMEA: "Europe, Middle East & Africa" },
      all_regions: false, unscoped: false, home: "EMEA", home_source: "sole_grant" },
    { email: "sam@example.com", name: "Sam Dobbins", job_title: "", photo: null,
      initials: "SO", hue: "teal", source: "sso",
      regions: [], region_names: {}, all_regions: false, unscoped: true,
      home: null, home_source: null },
  ],
};

function admin(caps: string[] = ["access_view", "access_edit"]): Me {
  return {
    user: "admin@example.com", role: "SuperAdmin", roles: ["SuperAdmin"],
    known_role: true, capabilities: caps, has_admin: true, can_edit_rules: true,
    evaluation_mode: true, review_pending: false, last_review: null,
  } as Me;
}

function setup(grants: unknown = GRANTS, me: Me = admin()) {
  const api = mockApi({ "/api/rbac": RBAC, "/api/access/grants": grants });
  renderPage(<ToastProvider><Access /></ToastProvider>, { me });
  return api;
}

beforeEach(() => resetApiMock());

describe("region grants", () => {
  it("lists people with their regions, and says who is not scoped", async () => {
    setup();
    expect(await screen.findByText("Bhavika Lillis")).toBeInTheDocument();
    expect(screen.getByText("Europe, Middle East & Africa")).toBeInTheDocument();
    // unscoped is UNRESTRICTED here, deliberately — and it says so
    expect(screen.getByText(/Not scoped — sees every region/)).toBeInTheDocument();
  });

  it("asks before narrowing someone who can currently see everything", async () => {
    const api = setup();
    await screen.findByText("Sam Dobbins");

    // Sam is the second row; each row carries its own "Add region"
    const adders = screen.getAllByRole("button", { name: /Add region/ });
    await userEvent.click(adders[1]);
    await userEvent.click(await screen.findByRole("menuitem", { name: "Americas" }));

    // nothing has happened yet — the narrowing case asks first
    expect(api.callsTo("/api/access/grants").filter((c) => c.method === "POST")).toHaveLength(0);
    expect(await screen.findByText(/Scope Sam Dobbins to Americas\?/)).toBeInTheDocument();
    expect(screen.getByText(/sees every region, because none is assigned/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Scope to Americas/ }));
    await waitFor(() => expect(
      api.callsTo("/api/access/grants").filter((c) => c.method === "POST")).toHaveLength(1));
    expect(api.callsTo("/api/access/grants")[1]?.body ??
           api.callsTo("/api/access/grants").find((c) => c.method === "POST")?.body)
      .toEqual({ email: "sam@example.com", region: "AMER" });
  });

  it("warns that removing the last region widens them again", async () => {
    setup();
    await screen.findByText("Bhavika Lillis");
    await userEvent.click(screen.getByRole("button",
      { name: "Remove Europe, Middle East & Africa" }));
    expect(await screen.findByText(/goes back to seeing every region/)).toBeInTheDocument();
  });

  it("is read-only without the capability", async () => {
    setup(GRANTS, admin(["access_view"]));
    await screen.findByText("Bhavika Lillis");
    expect(screen.queryByRole("button", { name: /Add region/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Remove / })).toBeNull();
  });

  it("explains itself when regions aren't switched on, rather than failing", async () => {
    setup({ status: "unseeded", message: "Region access isn't switched on for this deployment.",
            regions: [], people: [] });
    expect(await screen.findByText(/isn't switched on for this deployment/)).toBeInTheDocument();
  });

  it("explains itself when the database is unreachable", async () => {
    setup({ status: "unavailable", message: "The database isn't reachable right now.",
            regions: [], people: [] });
    expect(await screen.findByText(/database isn't reachable/)).toBeInTheDocument();
  });
});
