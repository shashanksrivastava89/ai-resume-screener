// The regional dial on the Jurisdictions pack editor (2026-09-12).
//
// `publish_pack` has always accepted `signal_overrides`; the editor never sent
// any, so a policy maker who wanted a signal turned off in their market had to
// edit YAML or call the API. The Scoring page cannot host this either — editing
// the model is a global act, and this is a change to one location's pack.
import { describe, it, expect, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SignalDial from "../components/SignalDial";
import { mockApi, renderPage, resetApiMock } from "./utils";
import { useState } from "react";
import type { SignalOverrides } from "../components/SignalDial";

const EFFECTIVE = {
  scope: "jurisdiction", jurisdiction: "SG", pack: "SG", notes: [],
  model: { version: 4, stamp: "v4 (abc)" }, pack_version: 1,
  structural: {
    scope: "global",
    bands: { strong_at: 85, possible_at: 60, cap_if_must_have_missing: "Possible" },
    clamp: { min: 0, max: 100 }, coverage: { match_value: { full: 1, partial: 0.5, none: 0 } },
    adjustments: { total_cap_points: 8 },
    preferences: { per_preference_cap_points: 2, review_months: 6 },
    confidence: {}, batch_quality: { top_n: 7, strong_at: 85, okay_at: 55 },
  },
  signals: [
    { id: "skill_recency", display_name: "Skill recency",
      description: "How recently the candidate used the skill.",
      kind: "judged", direction: "plus", retired: false, model_cap: 3,
      effective_cap: 3, enabled_in_model: true, enabled_here: true,
      origin: "model", enabled_origin: "model", cap_origin: "model",
      cap_raised: false, disabled_reason: null,
      params: { recent_years: 3 }, param_origins: { recent_years: "base" } },
    { id: "public_work_relevance", display_name: "Public work",
      description: "Relevant public work the candidate points to.",
      kind: "judged", direction: "plus", retired: false, model_cap: 2,
      effective_cap: null, enabled_in_model: false, enabled_here: false,
      origin: "model", enabled_origin: "model", cap_origin: "model",
      cap_raised: false, disabled_reason: "off in the global model",
      params: {}, param_origins: {} },
  ],
  critic: { universal_floor: [], model_topics: [], pack_exclusions: [],
            pack_blocked_patterns: [], critic_added_topics: [], total_never_scored: 9 },
};

function Harness() {
  const [value, setValue] = useState<SignalOverrides>({});
  return (
    <>
      <SignalDial code="SG" value={value} onChange={setValue} />
      <pre data-testid="overrides">{JSON.stringify(value)}</pre>
    </>
  );
}

function setup() {
  const api = mockApi({ "/api/scoring-model/effective": EFFECTIVE });
  renderPage(<Harness />);
  return api;
}

beforeEach(() => resetApiMock());

describe("the regional dial", () => {
  it("reads the effective values the run would use, and says what a location can change", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: /Show 2 signals/ }));
    expect(await screen.findByText("Skill recency")).toBeInTheDocument();
    // the model's own cap stays visible beside the editable one
    expect(screen.getByText("model ±3")).toBeInTheDocument();
  });

  it("sends nothing for a signal left at the model's settings", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: /Show 2 signals/ }));
    await screen.findByText("Skill recency");
    expect(screen.getByTestId("overrides")).toHaveTextContent("{}");
  });

  it("turns a signal off here without touching the model", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: /Show 2 signals/ }));
    await userEvent.click(await screen.findByLabelText("Skill recency fires in SG"));
    await waitFor(() => expect(screen.getByTestId("overrides"))
      .toHaveTextContent('"skill_recency":{"enabled":false}'));
    // both rows now say it: the one just switched off, and the one the model
    // already ships off
    expect(screen.getAllByText(/Will not fire for SG requisitions/)).toHaveLength(2);
  });

  it("warns in place when a cap goes above the company-wide one", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: /Show 2 signals/ }));
    const cap = await screen.findByLabelText("Skill recency cap in SG");
    await userEvent.clear(cap);
    await userEvent.type(cap, "5");
    expect(await screen.findByText(/Above the company-wide cap/)).toBeInTheDocument();
    expect(screen.getByText(/the approver will see a note/)).toBeInTheDocument();
  });

  it("lets a location switch on a signal the model ships off", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: /Show 2 signals/ }));
    await userEvent.click(await screen.findByLabelText("Public work fires in SG"));
    await waitFor(() => expect(screen.getByTestId("overrides"))
      .toHaveTextContent('"public_work_relevance":{"enabled":true}'));
  });
});
