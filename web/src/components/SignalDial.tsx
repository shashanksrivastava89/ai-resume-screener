// The regional dial: which of the company-wide model's AI signals fire in one
// jurisdiction, and how far each may move a score (2026-09-12).
//
// This is the ONLY place regional variation in scoring lives, and it had no
// surface at all: `governance.publish_pack` has always accepted
// `signal_overrides`, the Jurisdictions editor never sent any, so a policy maker
// who wanted to turn a signal off in their market had to edit YAML or call the
// API. The Scoring page cannot host it either — editing the model is a global
// act, and this is a change to one location's hiring-rules pack.
//
// Every value on screen comes from GET /api/scoring-model/effective, which
// derives them through the same call a screening run makes.
import { useState } from "react";
import { Field, Input, InfoDot } from "./ui";
import { useFetch } from "../hooks";
import type { EffectiveModelView, EffectiveSignalRow } from "../types";

export type SignalOverrides = Record<string, {
  enabled?: boolean; cap_points?: number; params?: Record<string, unknown>;
}>;

/** The market settings worth showing, in a recruiter's words. A key we have no
 *  label for still renders — better a raw key than a silently hidden setting. */
const PARAM_LABELS: Record<string, string> = {
  short_stint_months: "Short stint (months)",
  min_stints_for_pattern: "Stints before it counts as a pattern",
  lookback_years: "Look back (years)",
  recent_years: "“Recent” means within (years)",
  stale_after_years: "Credential goes stale after (years)",
};

function paramLabel(key: string): string {
  return PARAM_LABELS[key] ?? key.replace(/_/g, " ");
}

export default function SignalDial({ code, value, onChange }: {
  code: string;
  value: SignalOverrides;
  onChange: (next: SignalOverrides) => void;
}) {
  const { data: applied, error } = useFetch<EffectiveModelView>(
    `/api/scoring-model/effective?jurisdiction=${encodeURIComponent(code)}`);
  const [open, setOpen] = useState(false);

  if (error) return null;           // the rest of the editor still works
  if (!applied) return null;

  const rows = applied.signals.filter((s) => !s.retired);
  const touched = Object.keys(value).length;

  function set(id: string, patch: Record<string, unknown> | null) {
    const next = { ...value };
    if (patch === null) delete next[id];
    else next[id] = { ...(next[id] ?? {}), ...patch };
    // An override that says nothing is not an override.
    if (next[id] && Object.keys(next[id]).length === 0) delete next[id];
    onChange(next);
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
        <div style={{ fontSize: 13, fontWeight: 700, display: "flex", alignItems: "center", gap: 6 }}>
          AI signals in this jurisdiction
          <InfoDot
            label="What a location can change about scoring"
            content={
              <div style={{ maxWidth: 320 }}>
                The bands, the evidence credits, how far all AI signals may move a
                score together, the preference cap and the confidence rules belong to
                the company-wide model and are the same everywhere. What a location
                decides is on this list: which signals fire here, how far each may
                move a score, and the market settings behind them.
              </div>
            }
          />
        </div>
        <button type="button" className="link-quiet" onClick={() => setOpen((v) => !v)}>
          {open ? "Hide" : `Show ${rows.length} signals`}
          {touched > 0 && !open ? ` · ${touched} changed here` : ""}
        </button>
      </div>

      {open && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {rows.map((row) => (
            <SignalRow key={row.id} row={row} code={code}
                       override={value[row.id] ?? {}} onSet={(patch) => set(row.id, patch)} />
          ))}
        </div>
      )}
    </div>
  );
}

function SignalRow({ row, code, override, onSet }: {
  row: EffectiveSignalRow;
  code: string;
  override: { enabled?: boolean; cap_points?: number; params?: Record<string, unknown> };
  onSet: (patch: Record<string, unknown> | null) => void;
}) {
  const enabledHere = override.enabled ?? row.enabled_in_model;
  const cap = override.cap_points ?? row.model_cap;
  const raised = cap > row.model_cap;
  const params = row.params ?? {};

  return (
    <div style={{
      border: "1px solid var(--border-subtle)", borderRadius: 10, padding: "10px 12px",
      background: Object.keys(override).length ? "var(--surface-info, var(--neutral-100))" : "var(--white)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 13.5, fontWeight: 700 }}>
          <input
            type="checkbox"
            checked={enabledHere}
            aria-label={`${row.display_name} fires in ${code}`}
            onChange={(e) => onSet(
              e.target.checked === row.enabled_in_model ? { enabled: undefined }
                                                        : { enabled: e.target.checked })}
          />
          {row.display_name}
        </label>

        {enabledHere && (
          <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12.5 }}>
            <span style={{ color: "var(--ink-soft)" }}>may move a score by up to ±</span>
            <Input
              type="number"
              min={0}
              step={0.5}
              value={String(cap)}
              aria-label={`${row.display_name} cap in ${code}`}
              onChange={(e) => {
                const next = Number(e.target.value);
                onSet(Number.isNaN(next) || next === row.model_cap
                  ? { cap_points: undefined } : { cap_points: next });
              }}
              style={{ width: 74, height: 30 }}
            />
            <span style={{ color: "var(--ink-muted)" }}>
              model ±{row.model_cap}
            </span>
          </label>
        )}

        {Object.keys(override).length > 0 && (
          <button type="button" className="link-quiet" onClick={() => onSet(null)}>
            Use the model&rsquo;s settings
          </button>
        )}
      </div>

      {raised && (
        <div style={{ fontSize: 12, color: "var(--warn-ink)", marginTop: 6, fontWeight: 700 }}>
          Above the company-wide cap — allowed, and the approver will see a note.
        </div>
      )}
      {!enabledHere && (
        <div style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 6 }}>
          Will not fire for {code} requisitions.
        </div>
      )}
      {enabledHere && Object.keys(params).length > 0 && (
        <div style={{ marginTop: 8, display: "flex", gap: 12, flexWrap: "wrap" }}>
          {Object.entries(params)
            .filter(([, v]) => !Array.isArray(v))
            .map(([key, current]) => (
              <Field key={key} label={paramLabel(key)}
                     hint={row.param_origins?.[key] === "base" ? "from the defaults" : "set here"}>
                <Input
                  value={String((override.params ?? {})[key] ?? current)}
                  aria-label={`${row.display_name} — ${paramLabel(key)}`}
                  onChange={(e) => {
                    const raw = e.target.value;
                    const num = Number(raw);
                    onSet({ params: { ...(override.params ?? {}),
                                      [key]: raw === "" ? current : (Number.isNaN(num) ? raw : num) } });
                  }}
                  style={{ width: 110, height: 30 }}
                />
              </Field>
            ))}
        </div>
      )}
      <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 6, lineHeight: 1.45 }}>
        {row.description}
      </div>
    </div>
  );
}
