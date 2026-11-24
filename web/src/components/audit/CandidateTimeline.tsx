// Candidate audit timeline (P46, the "Candidates" tab): search a name across the
// private maps, then render Screened → HM take → Decision (verbatim reason) →
// Handed-off, with the design's action pills (Export audit pack · Score math —
// N/100 · View checklist · View JD). Dual-tz stamps reuse the shared formatter.
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { Link } from "react-router-dom";
import { get } from "../../api";
import { useFetch } from "../../hooks";
import { dualStamp } from "../../time";
import { Input } from "../ui";
import { IconSearch } from "../icons";
import { ScoreMathSection } from "../ScoreMathPanel";
import { OverlayRoot } from "../overlays";
import type { CandidateDetail, CandidateMatch, CandidateTimelineData } from "../../types";

async function downloadAuditPack(url: string, reqId: string) {
  const text = await get<string>(url);
  const href = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
  const a = document.createElement("a");
  a.href = href;
  a.download = `audit-${reqId}.md`;
  a.click();
  URL.revokeObjectURL(href);
}

const STEP_LABEL: Record<string, string> = {
  screened: "Screened",
  hm_take: "Hiring-manager take",
  decision: "Decision",
  handed_off: "Handed off",
};

// The audit page owns the RICH score-math surface (the design: hover-tip and
// drawer live in Review; the full as-of math modal lives here).
function ScoreMathModal({ tl, onClose }: { tl: CandidateTimelineData; onClose: () => void }) {
  const [detail, setDetail] = useState<CandidateDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    get<CandidateDetail>(`/api/roles/${tl.req_id}/review/${tl.candidate_id}`)
      .then((d) => { if (live) setDetail(d); })
      .catch((e) => { if (live) setError((e as Error).message); });
    return () => { live = false; };
  }, [tl.req_id, tl.candidate_id]);
  return (
    <OverlayRoot onDismiss={onClose} zIndex={70}>
      <div className="rise" role="dialog" aria-label={`Score math — ${tl.name}`}
        style={{ position: "relative", width: "min(600px, 94%)", maxHeight: "calc(100vh - 120px)", overflowY: "auto", background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, boxShadow: "var(--shadow-lg)", padding: "20px 24px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>Score math — {tl.name}</div>
          <button onClick={onClose} aria-label="Close score math" style={{ width: 30, height: 30, borderRadius: "50%", border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink-soft)", cursor: "pointer", fontSize: 13 }}>✕</button>
        </div>
        {error && <div style={{ fontSize: 13, color: "var(--warn-ink)" }}>{error}</div>}
        {!error && !detail && <div style={{ fontSize: 13, color: "var(--ink-soft)" }}>Loading…</div>}
        {detail?.score_math ? (
          <ScoreMathSection math={detail.score_math} />
        ) : detail && (
          <div style={{ fontSize: 13, color: "var(--ink-soft)" }}>
            The stored run doesn't carry a math trace for this candidate.
          </div>
        )}
      </div>
    </OverlayRoot>
  );
}

function TimelineView({ tl, reqTitle }: { tl: CandidateTimelineData; reqTitle?: string }) {
  const [mathOpen, setMathOpen] = useState(false);
  const screened = tl.steps.find((s) => s.step === "screened");
  const pill = (variant: "solid" | "green" | "quiet"): CSSProperties => ({
    height: 32, padding: "0 14px", borderRadius: 999, fontSize: 12, fontWeight: 700,
    cursor: "pointer", fontFamily: "inherit", display: "inline-flex", alignItems: "center",
    textDecoration: "none", whiteSpace: "nowrap",
    ...(variant === "solid"
      ? { border: 0, background: "var(--brand-green)", color: "#fff" }
      : variant === "green"
        ? { border: "1px solid var(--brand-green)", background: "var(--white)", color: "var(--brand-green-deep)" }
        : { border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink-soft)" }),
  });
  return (
    <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, padding: "20px 24px" }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--ink-soft)", marginBottom: 10 }}>
        Result · Candidate
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
        <span style={{ width: 36, height: 36, borderRadius: "50%", background: "var(--brand-green-tint)", color: "var(--brand-green-deep)", display: "inline-flex", alignItems: "center", justifyContent: "center", fontWeight: 700, fontSize: 12.5 }}>
          {tl.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase()}
        </span>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700 }}>{tl.name}</div>
          <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 1 }}>{reqTitle || tl.req_id}</div>
        </div>
      </div>

      {/* the design's left-rail timeline: a 2px spine, dots per step */}
      <div style={{ borderLeft: "2px solid var(--border-subtle)", marginLeft: 17, paddingLeft: 20, display: "flex", flexDirection: "column", gap: 14 }}>
        {tl.steps.map((s, i) => {
          const dot = s.step === "hm_take" ? "var(--accent-blue)" : s.step === "handed_off" ? "var(--border-strong)" : "var(--brand-green)";
          return (
            <div key={i} style={{ position: "relative" }}>
              <span style={{ position: "absolute", left: -27, top: 3, width: 11, height: 11, borderRadius: "50%", background: dot, border: "2.5px solid var(--white)" }} />
              <div style={{ fontSize: 13, fontWeight: 700 }}>
                {STEP_LABEL[s.step] ?? s.step}
                {s.step === "screened" && s.score != null && ` — ${s.score}/100 · ${s.band}${s.gates?.failed?.length ? ` · gate not met: ${s.gates.failed.join(", ")}` : ""}`}
                {s.step === "hm_take" && ` — ${s.take}`}
                {s.step === "decision" && ` — ${s.action}`}
              </div>
              <div style={{ fontSize: 11.5, color: "var(--ink-soft)", marginTop: 1 }}>
                {s.step === "screened" && (
                  <>
                    {s.checklist_version ? `checklist ${s.checklist_version}` : ""}{s.pack ? ` · ${s.pack} pack` : ""}
                    {s.scoring_model ? ` · scoring model ${s.scoring_model.split(" ")[0]}` : ""}
                  </>
                )}
                {s.step === "hm_take" && s.who}
                {s.step === "decision" && s.who}
                {s.step === "handed_off" && s.note}
                {s.ts ? `${s.step === "handed_off" && !s.note ? "" : " · "}${dualStamp(s.ts, tl.tz)}` : ""}
              </div>
              {s.step === "decision" && s.reason && (
                <div style={{ fontSize: 12, fontStyle: "italic", background: "var(--neutral-100)", borderRadius: 8, padding: "6px 10px", marginTop: 5 }}>
                  "{s.reason}"
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* the design's action pills */}
      <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
        <button onClick={() => downloadAuditPack(tl.targets.audit_pack_url, tl.req_id)} style={pill("solid")}>
          Export audit pack (PDF)
        </button>
        {screened?.score != null && (
          <button onClick={() => setMathOpen(true)} style={pill("green")}>
            Score math — {screened.score}/100
          </button>
        )}
        <Link to={`/reqs/${tl.req_id}/checklist`} style={pill("quiet")}>
          View checklist{tl.targets.checklist_version ? ` ${tl.targets.checklist_version}` : ""}
        </Link>
        <Link to={`/reqs/${tl.req_id}/overview`} style={pill("quiet")}>View JD</Link>
        <Link to={`/reqs/${tl.req_id}/review`} style={pill("quiet")}>View run</Link>
      </div>

      {mathOpen && <ScoreMathModal tl={tl} onClose={() => setMathOpen(false)} />}
    </div>
  );
}

export default function CandidateTimeline({ region, seedQuery = "" }: { region: string | null; seedQuery?: string }) {
  const [q, setQ] = useState(seedQuery);
  const [selected, setSelected] = useState<CandidateMatch | null>(null);

  // the page-level search seeds this tab's candidate lookup
  useEffect(() => { if (seedQuery) setQ(seedQuery); }, [seedQuery]);

  const searchQs = new URLSearchParams({ q });
  if (region) searchQs.set("region", region);
  const { data: search } = useFetch<{ matches: CandidateMatch[] }>(
    q.trim() ? `/api/audit/candidates?${searchQs.toString()}` : null
  );
  const { data: tl } = useFetch<CandidateTimelineData>(
    selected ? `/api/audit/candidates/${selected.req_id}/${selected.candidate_id}/timeline` : null
  );

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(240px, 320px) 1fr", gap: 20, alignItems: "start" }}>
      <div>
        <div style={{ position: "relative", marginBottom: 12 }}>
          <span style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--ink-muted)" }}><IconSearch size={15} /></span>
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search a candidate by name" style={{ paddingLeft: 34 }} />
        </div>
        <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 14, overflow: "hidden" }}>
          {!q.trim() && <div style={{ padding: "16px", fontSize: 12.5, color: "var(--ink-soft)" }}>Type a name to search the private maps.</div>}
          {q.trim() && (search?.matches.length ?? 0) === 0 && <div style={{ padding: "16px", fontSize: 12.5, color: "var(--ink-soft)" }}>No matches.</div>}
          {(search?.matches ?? []).map((m) => (
            <button
              key={`${m.req_id}:${m.candidate_id}`}
              onClick={() => setSelected(m)}
              style={{ display: "block", width: "100%", textAlign: "left", padding: "11px 14px", border: "none", borderBottom: "1px solid var(--neutral-150)", background: selected?.candidate_id === m.candidate_id && selected?.req_id === m.req_id ? "var(--neutral-100)" : "var(--white)", cursor: "pointer" }}
            >
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>{m.name}</div>
              <div style={{ fontSize: 12, color: "var(--ink-soft)" }}>{m.req_title}</div>
            </button>
          ))}
        </div>
      </div>

      <div>
        {selected && tl ? (
          <TimelineView tl={tl} reqTitle={selected?.req_title} />
        ) : (
          <div style={{ padding: "40px 20px", fontSize: 13, color: "var(--ink-soft)", textAlign: "center" }}>
            Search and pick a candidate to see their screening → decision timeline.
          </div>
        )}
      </div>
    </div>
  );
}
