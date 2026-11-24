// New-requisition intake helpers: the careers-portal auto-fill (temporary iCIMS
// bridge, flag-gated) on iCIMS-field blur, and the always-on JD upload. Both
// pre-fill the form and never clobber what the recruiter already typed.
import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import NewRequisition from "../pages/NewRequisition";
import { mockApi, renderPage } from "./utils";
import type { CareersLookupResult, FeatureFlag, JdParseResult } from "../types";
import { LOCATIONS_FEED } from "./fixtures";

const LOCATIONS = LOCATIONS_FEED;

function features(careersOn: boolean): { features: FeatureFlag[] } {
  return {
    features: [{
      key: "careers_autofill", name: "Careers auto-fill", description: "",
      default: false, enabled: careersOn, control: "env", env: "FEATURE_CAREERS_AUTOFILL",
      source: "file", regions: {},
    } as FeatureFlag],
  };
}

function setup(routes: Record<string, unknown>) {
  const api = mockApi({ "/api/locations": LOCATIONS, ...routes });
  renderPage(
    <Routes><Route path="/reqs/new" element={<NewRequisition />} /></Routes>,
    { route: "/reqs/new" }
  );
  return api;
}

describe("careers-portal auto-fill (temporary iCIMS bridge)", () => {
  it("fills title, JD and location on blur when the flag is on", async () => {
    const result: CareersLookupResult = {
      status: "ok", title: "Sr Accountant - GL Accounting",
      jd_text: "Own the monthly GL close.", job_location: "EU",
      location_label: "Budapest, Hungary", country: "HU",
    };
    const api = setup({
      "/api/features": features(true),
      "/api/careers/lookup": result,
    });
    // wait for the flag to load (the live-lookup hint only renders when it's on)
    await screen.findByText(/fill in from the careers site/);
    await userEvent.type(screen.getByLabelText("iCIMS Req ID"), "2026-38563");
    await userEvent.tab(); // blur the iCIMS field

    await waitFor(() => expect(screen.getByLabelText("Job title")).toHaveValue("Sr Accountant - GL Accounting"));
    expect(screen.getByLabelText("Job description")).toHaveValue("Own the monthly GL close.");
    expect(screen.getByLabelText("Country")).toHaveValue("HU");
    expect(screen.getByText(/Filled from the careers site/)).toBeInTheDocument();
    expect(api.callsTo("/api/careers/lookup")).toHaveLength(1);
  });

  it("shows a fill-manually note and keeps fields empty when not found", async () => {
    setup({
      "/api/features": features(true),
      "/api/careers/lookup": { status: "not_found", note: "No posting found for 2026-9999 — enter the details below." } as CareersLookupResult,
    });
    await screen.findByText(/fill in from the careers site/);
    await userEvent.type(screen.getByLabelText("iCIMS Req ID"), "2026-9999");
    await userEvent.tab();
    expect(await screen.findByText(/No posting found for 2026-9999/)).toBeInTheDocument();
    expect(screen.getByLabelText("Job title")).toHaveValue("");
  });

  it("does not look up when the flag is off", async () => {
    const api = setup({ "/api/features": features(false) });
    await screen.findByLabelText(/Country/); // page settled
    await userEvent.type(screen.getByLabelText("iCIMS Req ID"), "2026-38563");
    await userEvent.tab();
    expect(api.callsTo("/api/careers/lookup")).toHaveLength(0);
  });
});

describe("JD upload (always-on)", () => {
  it("parses an uploaded JD into the title, JD and location", async () => {
    const parsed: JdParseResult = {
      title: "Accountant / Sr. Accountant, RTR - GL Accounting",
      jd_text: "Maintains ledger accounts and reconciliations.",
      job_location: "IN", location_label: "Gurgaon, India",
    };
    setup({ "/api/features": features(false), "/api/jd/parse": parsed });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File([new Uint8Array([1, 2, 3])], "Accountant.docx",
                          { type: "application/octet-stream" });
    await userEvent.upload(input, file, { applyAccept: false });

    await waitFor(() => expect(screen.getByLabelText("Job title"))
      .toHaveValue("Accountant / Sr. Accountant, RTR - GL Accounting"));
    expect(screen.getByLabelText("Job description")).toHaveValue("Maintains ledger accounts and reconciliations.");
    expect(screen.getByLabelText("Country")).toHaveValue("");
    expect(screen.getByText(/Read .Accountant\.docx/)).toBeInTheDocument();
  });
});
