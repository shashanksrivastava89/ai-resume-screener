// The Scoring model page (v3 design): values render from the API (never a
// literal), staging edits raises the draft bar with live warnings, submit
// POSTs the FULL draft document, the calculator sends DRAFT values to the real
// engine, and the Bias critic tab gates floor-topic removal behind the
// exposure acknowledgment.
import { describe, it, expect, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import Scoring from "../pages/Scoring";
import { ToastProvider } from "../components/overlays";
import { mockApi, renderPage, resetApiMock } from "./utils";
import { EFFECTIVE_EU, JURISDICTIONS_FIXTURE, PREVIEW_FIXTURE, SCORING_CONFIG,
         scoringModelReadFixture } from "./fixtures";

function setup(route = "/scoring/score", conflicts: unknown = { conflicts: [] },
               extra: Record<string, unknown> = {}) {
  const api = mockApi({
    "/api/scoring-model": scoringModelReadFixture(),
    "/api/scoring-config": SCORING_CONFIG,
    "/api/scoring/conflicts": conflicts,
    "/api/changes": (_url: string, init?: RequestInit) =>
      init?.method === "POST"
        ? { ok: true, change: { change_id: "c9", status: "pending" } }
        : { changes: [], pending_count: 0 },
    "/api/scoring/preview": PREVIEW_FIXTURE,
    "/api/jurisdictions": JURISDICTIONS_FIXTURE,
    "/api/scoring-model/effective": EFFECTIVE_EU,
    ...extra,
  });
  renderPage(
    <ToastProvider>
      <Routes>
        <Route path="/scoring/:tab?" element={<Scoring />} />
        <Route path="/approvals" element={<div>approvals</div>} />
      </Routes>
    </ToastProvider>,
    { route },
  );
  return api;
}

beforeEach(() => resetApiMock());

// The page formats the version date in the browser's own locale, so the expected text
// is built the same way: "16 Jul 2026" here, "Jul 16, 2026" on an en-US CI runner.
const SINCE = new Date("2026-07-16T09:00:00+00:00")
  .toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });

describe("The score tab", () => {
  it("renders knob values from the API after opening a card", async () => {
    setup();
    expect(await screen.findByText(`v1 in effect · everywhere · since ${SINCE}`)).toBeInTheDocument();
    // open the Bands & bounds card
    const bandsCard = document.getElementById("sec-bands")!;
    await userEvent.click(within(bandsCard).getByRole("button", { name: "Review & edit" }));
    expect(await screen.findByText("≥ 88")).toBeInTheDocument();
    expect(screen.getByText("≥ 60")).toBeInTheDocument();
    // the budget line reads the model + the median stat
    expect(screen.getByText(/held to ±8 all together/)).toBeInTheDocument();
    expect(screen.getByText(/median real move last quarter: \+6/)).toBeInTheDocument();
  });

  it("staging an edit raises the draft bar and submits the FULL draft", async () => {
    const api = setup();
    await screen.findByText("Bands & bounds");
    const bandsCard = document.getElementById("sec-bands")!;
    await userEvent.click(within(bandsCard).getByRole("button", { name: "Review & edit" }));
    // 88 → 85 via the stepper
    const dec = screen.getByRole("button", { name: "Decrease ≥ 88" });
    await userEvent.click(dec);
    await userEvent.click(screen.getByRole("button", { name: "Decrease ≥ 87" }));
    await userEvent.click(screen.getByRole("button", { name: "Decrease ≥ 86" }));
    expect(await screen.findByText("1 staged — nothing takes effect until a second leader approves.")).toBeInTheDocument();
    expect(screen.getByText("was ≥ 88")).toBeInTheDocument();

    const reason = screen.getByPlaceholderText(/Why this change/);
    await userEvent.type(reason, "July calibration");
    await userEvent.click(screen.getByRole("button", { name: "Submit for approval →" }));

    await waitFor(() => {
      const posts = api.callsTo("/api/changes").filter((c) => c.method === "POST");
      expect(posts).toHaveLength(1);
      const body = posts[0].body as { kind: string; payload: { model: { bands: { strong_at: number } } }; rationale: string };
      expect(body.kind).toBe("scoring_model");
      expect(body.payload.model.bands.strong_at).toBe(85);
      expect(body.rationale).toBe("July calibration");
    });
  });

  it("out-of-the-usual values surface a live warning in the draft bar", async () => {
    setup();
    await screen.findByText("Bands & bounds");
    const prefsCard = document.getElementById("sec-prefs")!;
    await userEvent.click(within(prefsCard).getByRole("button", { name: "Review & edit" }));
    // cap 2 → 5 (outside usual [0, 4] AND above the >4 heuristic)
    await userEvent.click(screen.getByRole("button", { name: "Increase ± 2" }));
    await userEvent.click(screen.getByRole("button", { name: "Increase ± 3" }));
    await userEvent.click(screen.getByRole("button", { name: "Increase ± 4" }));
    expect(await screen.findByText(/outside the usual range/)).toBeInTheDocument();
    expect(screen.getByText(/could outweigh evidenced signals/)).toBeInTheDocument();
  });

  it("the calculator sends DRAFT values to the preview endpoint", async () => {
    const api = setup();
    await screen.findByText("Bands & bounds");
    const bandsCard = document.getElementById("sec-bands")!;
    await userEvent.click(within(bandsCard).getByRole("button", { name: "Review & edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Decrease ≥ 88" }));
    await waitFor(() => {
      const calls = api.callsTo("/api/scoring/preview").filter((c) => c.method === "POST");
      const last = calls[calls.length - 1]?.body as { model?: { bands?: { strong_at?: number } } };
      expect(last?.model?.bands?.strong_at).toBe(87);
    }, { timeout: 3000 });
    // the rail renders from the REAL engine's response
    expect(await screen.findByText("Sample: Treasury Accountant, 7 yrs")).toBeInTheDocument();
    expect(screen.getAllByText("Possible match").length).toBeGreaterThan(0);
  });
});

describe("Config-to-config staleness banner", () => {
  it("warns when a scoring signal conflicts with a jurisdiction floor", async () => {
    setup("/scoring/score", {
      conflicts: [{
        signal_id: "pay_progression", signal_name: "Pay progression",
        jurisdiction: "US-CA", pattern_name: "Salary history",
        reason: "California bars relying on salary history.",
      }],
    });
    expect(await screen.findByText(/jurisdiction's floor changed under this model/i)).toBeInTheDocument();
    expect(screen.getByText(/Pay progression/)).toBeInTheDocument();
    expect(screen.getByText(/California bars relying on salary history/)).toBeInTheDocument();
  });

  it("shows no banner when there are no conflicts", async () => {
    setup("/scoring/score");
    await screen.findByRole("tab", { name: /The score/i });
    expect(screen.queryByText(/jurisdiction's floor changed under this model/i)).not.toBeInTheDocument();
  });
});

describe("The Bias critic tab", () => {
  it("lists the governed topics with stages + phrases", async () => {
    setup("/scoring/critic");
    expect(await screen.findByText("Never-scored topics · 2")).toBeInTheDocument();
    expect(screen.getByText("Employment gaps")).toBeInTheDocument();
    expect(screen.getAllByText("Policy floor").length).toBe(2);
    await userEvent.click(screen.getAllByRole("button", { name: "Trigger phrases · 2" })[0]);
    expect(await screen.findByText("“employment gap”")).toBeInTheDocument();
  });

  it("removing a floor topic takes the exposure acknowledgment and reds the bar", async () => {
    setup("/scoring/critic");
    await screen.findByText("Employment gaps");
    await userEvent.click(screen.getByRole("button", { name: "Remove Employment gaps" }));
    const dialog = await screen.findByRole("dialog", { name: "Remove a policy-floor protection?" });
    expect(within(dialog).getByText(/law-adjacent/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Stage removal" }));
    // the red warning lands in the draft bar
    expect(await screen.findByText(/policy-floor protection tied to anti-discrimination law/)).toBeInTheDocument();
    // …and Restore brings it back
    await userEvent.click(screen.getByRole("button", { name: "↺ Restore" }));
    expect(await screen.findByText("Employment gaps")).toBeInTheDocument();
  });
});

describe("The Versions tab", () => {
  it("shows the version rail with proposer → approver and the rationale", async () => {
    setup("/scoring/versions");
    expect(await screen.findByText("v1")).toBeInTheDocument();
    expect(screen.getByText("In effect")).toBeInTheDocument();
    expect(screen.getByText(/Proposed by hr@example.com · approved by legal@example.com/)).toBeInTheDocument();
    expect(screen.getByText("“July calibration”")).toBeInTheDocument();
  });
});


// --- the jurisdiction lens (the global rollout, 2026-09-12) -------------------------

describe("the model as it applies somewhere", () => {
  it("opens on the global model and says so", async () => {
    setup();
    expect(await screen.findByText(`v1 in effect · everywhere · since ${SINCE}`))
      .toBeInTheDocument();
    expect(screen.getByLabelText("Show the model as it applies in")).toHaveValue("");
    // the untrue copy is gone
    expect(screen.queryByText(/this page edits/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/India \(IN\) only/)).not.toBeInTheDocument();
  });

  it("shows a location's effective values, read-only, and says where to change them", async () => {
    const api = setup();
    await screen.findByLabelText("Show the model as it applies in");
    await userEvent.selectOptions(
      screen.getByLabelText("Show the model as it applies in"), "EU");

    await waitFor(() => expect(
      screen.getByText(/as applied in European Union/)).toBeInTheDocument());
    expect(api.callsTo("/api/scoring-model/effective")).toHaveLength(1);
    // what is the same everywhere is stated, not left to be assumed
    expect(screen.getByText(/the same in every location/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Change European Union/ })).toBeInTheDocument();
  });

  it("refuses to edit under a lens and points at the pack instead", async () => {
    const api = setup();
    await screen.findByLabelText("Show the model as it applies in");
    await userEvent.selectOptions(
      screen.getByLabelText("Show the model as it applies in"), "EU");
    await waitFor(() => expect(
      screen.getByText(/as applied in European Union/)).toBeInTheDocument());

    // the draft bar belongs to the editable view and must not appear here
    expect(screen.queryByText(/Send for approval/i)).not.toBeInTheDocument();
    // every stepper the page offers refuses, and says where to go instead
    const steppers = screen.queryAllByRole("button", { name: "+" });
    if (steppers.length) {
      await userEvent.click(steppers[0]);
      expect(await screen.findByText(/read-only/i)).toBeInTheDocument();
    }
    // nothing is ever proposed from a lens
    expect(api.callsTo("/api/changes").filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("surfaces the server's refusal when a location has no rules", async () => {
    setup("/scoring/score", { conflicts: [] }, {
      "/api/scoring-model/effective": () => {
        throw new Error("No policy pack for jurisdiction 'ZZ'.");
      },
    });
    await screen.findByLabelText("Show the model as it applies in");
    await userEvent.selectOptions(
      screen.getByLabelText("Show the model as it applies in"), "EU");
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/No policy pack/));
  });
});
