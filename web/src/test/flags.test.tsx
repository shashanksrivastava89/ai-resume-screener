// P37: the Feature-flags page. A Super Admin (flags_edit) toggles normal flags;
// env-controlled flags render read-only ("Controlled by deployment"); a viewer
// with flags_view only sees the switches disabled.
import { describe, it, expect } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Flags from "../pages/Flags";
import { mockApi, renderPage } from "./utils";
import type { FeatureFlag, Me } from "../types";

const FEATURES: FeatureFlag[] = [
  { key: "icims_reopen", name: "Reopen requisition", description: "…", default: false,
    enabled: false, regions: {}, scope: "requisition", source: "file" },
  { key: "careers_autofill", name: "Careers-portal auto-fill", description: "…", default: false,
    enabled: false, regions: {}, scope: "app", source: "file",
    control: "env", env: "FEATURE_CAREERS_AUTOFILL" },
];

function meWith(caps: string[]): Me {
  return {
    user: "sa@example.com", role: "SuperAdmin", roles: ["SuperAdmin"], known_role: true,
    capabilities: caps, has_admin: true, can_edit_rules: false, evaluation_mode: true,
    review_pending: false, last_review: null, regions: { all: false, codes: ["IN", "EMEA"] },
  };
}

describe("Feature flags page", () => {
  it("Super Admin toggles a normal flag → PUT /api/flags/{key}; env flag is read-only", async () => {
    const api = mockApi({
      "/api/features": { features: FEATURES },
      "/api/flags/icims_reopen": { ok: true, flag: { ...FEATURES[0], default: true, enabled: true } },
    });
    renderPage(<Flags />, { route: "/flags", me: meWith(["flags_view", "flags_edit"]) });

    expect(await screen.findByText("Reopen requisition")).toBeInTheDocument();
    // the env-controlled flag is read-only
    expect(screen.getByText("Controlled by deployment (env var)")).toBeInTheDocument();

    // exactly one editable switch (the env flag renders a chip, not a switch)
    const switches = screen.getAllByRole("switch");
    expect(switches).toHaveLength(1);
    await userEvent.click(switches[0]);

    const put = api.callsTo("/api/flags/icims_reopen").find((c) => c.method === "PUT");
    expect(put?.body).toEqual({ enabled: true });
  });

  it("per-region toggles reflect override-else-default", async () => {
    mockApi({
      "/api/features": { features: [{ ...FEATURES[0], default: true, enabled: true, regions: { EMEA: false } }] },
    });
    renderPage(<Flags />, { route: "/flags", me: meWith(["flags_view", "flags_edit"]) });
    expect(await screen.findByText("By region")).toBeInTheDocument();
    const inRow = screen.getByText("IN").closest("label") as HTMLElement;
    const emeaRow = screen.getByText("EMEA").closest("label") as HTMLElement;
    // IN inherits the default (on); EMEA has an explicit off override
    expect(within(inRow).getByRole("checkbox")).toBeChecked();
    expect(within(emeaRow).getByRole("checkbox")).not.toBeChecked();
  });

  it("a viewer without flags_edit sees disabled switches", async () => {
    mockApi({ "/api/features": { features: FEATURES } });
    renderPage(<Flags />, { route: "/flags", me: meWith(["flags_view"]) });
    expect(await screen.findByText("Reopen requisition")).toBeInTheDocument();
    expect(screen.getByRole("switch")).toBeDisabled();
  });

  it("offers per-region toggles only for a flag that can actually differ there", async () => {
    // A per-region override is only ever consulted where a call site has a
    // REQUISITION to take a region from. Every non-env flag used to get this row,
    // which promised something the code cannot keep (2026-09-12).
    mockApi({
      "/api/features": { features: [
        { ...FEATURES[0], scope: "app", key: "manual_locks", name: "Manual locks" },
      ] },
    });
    renderPage(<Flags />, { route: "/flags", me: meWith(["flags_view", "flags_edit"]) });
    expect(await screen.findByText(/Applies everywhere/)).toBeInTheDocument();
    expect(screen.queryByText("By region")).toBeNull();
  });
});
