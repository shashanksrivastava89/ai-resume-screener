// "Versions" tab (v3 design): pending proposals first, then every published
// version — summary, the proposer's reasoning verbatim, who approved, and
// "Restore this version" (stages the old document as a draft; a second leader
// must approve again).
import type { ChangeRequest, ModelVersionRow, ScoringDoc } from "../../types";
import { get } from "../../api";
import { useToast } from "../overlays";
import { durationText, formatDay } from "./model";

const chip = (bg: string, fg: string): React.CSSProperties => ({
  display: "inline-flex", alignItems: "center", fontSize: 11.5, fontWeight: 700,
  padding: "3px 10px", borderRadius: 999, background: bg, color: fg, whiteSpace: "nowrap",
});

export default function VersionsTab({ versions, currentVersion, pending, onRestore }: {
  versions: ModelVersionRow[];
  currentVersion: number;
  pending: ChangeRequest[];
  onRestore: (doc: ScoringDoc, reason: string) => void;
}) {
  const toast = useToast();

  const restore = async (n: number, when: string, until: string | null) => {
    const payload = await get<{ version: number; model: ScoringDoc }>(`/api/scoring-model/versions/${n}`);
    onRestore(
      payload.model,
      `Restore the v${n} configuration (in effect ${when} — ${until ?? "today"})`,
    );
    toast(`v${n} staged as a draft — nothing changes until a second leader approves`);
  };

  return (
    <>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {pending.map((q) => (
          <div key={q.change_id} style={{ background: "var(--white)", border: "1.5px solid var(--accent-yellow)", borderRadius: 14, padding: "15px 20px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <span style={{ fontSize: 14, fontWeight: 700 }}>v{q.base_version + 1}</span>
              <span style={chip("var(--surface-warn)", "var(--warn-ink)")}>Pending approval</span>
              <span style={{ fontSize: 11.5, color: "var(--ink-muted)" }}>{formatDay(q.proposed_at)}</span>
            </div>
            <div style={{ fontSize: 12.5, marginTop: 6, lineHeight: 1.5 }}>{q.summary.join(" · ")}</div>
            <div style={{ fontSize: 12, fontStyle: "italic", color: "var(--ink-soft)", marginTop: 4, lineHeight: 1.5 }}>
              “{q.rationale}”
            </div>
            <div style={{ fontSize: 11.5, color: "var(--ink-soft)", marginTop: 6 }}>
              Proposed by {q.proposed_by} · awaiting a second leader
            </div>
          </div>
        ))}

        {versions.map((v, i) => {
          const isCurrent = v.version === currentVersion;
          const supersededBy = i > 0 ? versions[i - 1] : null;
          const when = formatDay(v.ts) || "the pilot rollout";
          const until = supersededBy ? formatDay(supersededBy.ts) : null;
          const canRestore = !isCurrent && !!supersededBy;
          return (
            <div key={v.version} style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 14, padding: "15px 20px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <span style={{ fontSize: 14, fontWeight: 700 }}>v{v.version}</span>
                {isCurrent
                  ? <span style={chip("var(--brand-green-tint)", "var(--brand-green-deep)")}>In effect</span>
                  : <span style={chip("var(--neutral-150)", "#5A5A5A")}>Superseded</span>}
                <span style={{ fontSize: 11.5, color: "var(--ink-muted)" }}>{when}</span>
              </div>
              <div style={{ fontSize: 12.5, marginTop: 6, lineHeight: 1.5 }}>
                {v.summary || "Pilot defaults — deterministic-first, the seeded signals and never-scored topics."}
              </div>
              {v.rationale && (
                <div style={{ fontSize: 12, fontStyle: "italic", color: "var(--ink-soft)", marginTop: 4, lineHeight: 1.5 }}>
                  “{v.rationale}”
                </div>
              )}
              <div style={{ fontSize: 11.5, color: "var(--ink-soft)", marginTop: 6 }}>
                {v.author
                  ? <>Proposed by {v.author} · approved by {v.approved_by ?? "direct publish"}</>
                  : <>Pilot rollout · the seeded configuration</>}
              </div>
              {canRestore && (
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10, borderTop: "1px solid var(--neutral-150)", paddingTop: 9, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 11.5, color: "var(--ink-soft)", lineHeight: 1.5, flex: "1 1 260px" }}>
                    This exact configuration {v.ts ? `was approved ${when} and ` : ""}stayed in effect
                    {v.ts && supersededBy?.ts ? ` for ${durationText(v.ts, supersededBy.ts)}` : ""} — until {until}.
                    Restoring stages it as a draft; a second leader must approve again.
                  </span>
                  <button
                    onClick={() => restore(v.version, when, until)}
                    style={{ height: 30, padding: "0 13px", borderRadius: 999, border: "1.5px solid var(--brand-green)", background: "var(--white)", color: "var(--brand-green-deep)", fontFamily: "inherit", fontSize: 11.5, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap" }}
                  >
                    ↺ Restore this version
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div style={{ fontSize: 11.5, color: "var(--ink-muted)", marginTop: 12, lineHeight: 1.55 }}>
        Every score records the version it was computed with. When a new version lands, requisitions with no recorded decisions refresh automatically; those mid-review are marked stale and re-screen on demand.
      </div>
    </>
  );
}
