// The per-requisition workspace (Addendum 11 Part A1, P31): one shareable URL
// per requisition at /reqs/:reqId/:tab? with tabs Overview · Checklist ·
// Candidates · Review · Shortlist · Audit. TABS is the ONE tab registry later
// prompts append to (Part D3); tab panels read {role, blind, setBlind, reload}
// from the router Outlet context. The Ask Claude button arrives in P44.
import { useEffect, useRef, useState } from "react";
import { Outlet, Route, useLocation, useNavigate, useParams } from "react-router-dom";
import type { ReactElement } from "react";
import { Page } from "../../layout/AppShell";
import { Tabs, BackButton, PageError, Button, Switch, HeaderSkeleton } from "../../components/ui";
import { IconSparkle, IconPin, IconChecklist, IconFile } from "../../components/icons";
import AskClaudePanel from "../../components/AskClaudePanel";
import { ConfirmModal } from "../../components/overlays";
import { STAGE_CHIP } from "../../stages";
import { get, post } from "../../api";
import { useFetch, usePolling, useFeatureFlag } from "../../hooks";
import { useMe } from "../../MeContext";
import { isHmActive } from "../../hm";
import { canSeeVerdict } from "../../verdict";
import { Person, usePeople } from "../../people";
import { useConfirmLeave } from "../../leave";
import type { WorkspaceContext } from "./context";
import type { RoleDetail, ScreenJob } from "../../types";
import OverviewTab from "./OverviewTab";
import ChecklistTab from "./ChecklistTab";
import CandidatesTab from "./CandidatesTab";
import ReviewTab from "./ReviewTab";
import ShortlistTab from "./ShortlistTab";
import AuditTab from "./AuditTab";

export interface WorkspaceTab {
  key: string;
  label: string;
  /** The tab's hue (2026-09-06): the tab dot/underline and a faint wash on the canvas
   *  while it is open, so "which page am I on" is answered by colour. The order is
   *  the stage progression the dashboard chips use: blue (information) → teal
   *  (configuration) → orange (activity) → violet (people) → green (done) → slate. */
  hue?: string;
  /** Optional count pill, derived from the role detail. */
  count?: (role: RoleDetail) => number | undefined;
  /** When it returns a string, the tab is disabled and the string is its tooltip. */
  disabled?: (role: RoleDetail) => string | undefined;
  element: ReactElement;
}

// The single tab registry — later prompts append entries here; never fork a
// second tab mechanism (Part D3). Review + Shortlist stay disabled (with a
// plain-language tooltip) until a screening run exists — the same "don't offer
// what isn't ready yet" idea as the Shortlist button waiting on a decision.
export const TABS: WorkspaceTab[] = [
  { key: "overview", label: "Overview", hue: "blue", element: <OverviewTab /> },
  { key: "checklist", label: "Checklist", hue: "teal", count: (r) => r.requirement_count || undefined, element: <ChecklistTab /> },
  { key: "candidates", label: "Candidates", hue: "orange", count: (r) => r.batch_count || undefined, element: <CandidatesTab /> },
  { key: "review", label: "Review", hue: "violet", count: (r) => (r.screened ? r.candidate_count : undefined),
    disabled: (r) => (r.screened ? undefined : "Screen candidates first — the ranked shortlist appears here."),
    element: <ReviewTab /> },
  { key: "shortlist", label: "Shortlist", hue: "green", count: (r) => r.reviewed_count || undefined,
    disabled: (r) => (r.screened ? undefined : "Review the shortlist first — your decisions appear here."),
    element: <ShortlistTab /> },
  { key: "audit", label: "Audit", hue: "slate", element: <AuditTab /> },
];

// The workspace route subtree — shared by App.tsx and the tests so the two
// can't drift. /reqs/:reqId (no tab segment) renders Overview.
export function workspaceRoutes(): ReactElement {
  return (
    <Route path="/reqs/:reqId" element={<Workspace />}>
      <Route index element={TABS[0].element} />
      {TABS.map((t) => (
        <Route key={t.key} path={t.key} element={t.element} />
      ))}
    </Route>
  );
}

function outcomeSummary(outcome: { kind: string; hired_count?: number } | null): string {
  if (!outcome) return "";
  return outcome.kind === "hired" ? ` — hired ${outcome.hired_count ?? 1}` : " — no hire (cancelled)";
}

// Poll this requisition's in-flight screen so the banner stays live on EVERY tab
// (the run never depended on the Candidates tab being open). When a run finishes,
// refresh the role so the stage chip + counts catch up.
function useReqScreen(reqId: string, onDone: () => void): ScreenJob | null {
  const [job, setJob] = useState<ScreenJob | null>(null);
  const wasRunning = useRef(false);
  // Fast only while this requisition's run is live (or was just started from the
  // Candidates tab, which calls bumpPolling), slow otherwise, paused while hidden.
  // This used to fire every 2 s on every tab for as long as the workspace was open
  // — the single largest source of background load on the hosted app.
  usePolling(async () => {
    const r = await get<{ job: ScreenJob | null }>(`/api/roles/${reqId}/screen/active`);
    const running = r.job?.status === "running" ? r.job : null;
    setJob(running);
    if (wasRunning.current && !running) onDone(); // a run just landed
    wasRunning.current = !!running;
    return !!running;
  }, true, [reqId, onDone]);
  return job;
}

// The compact live-screening pill in the title row — screening is the product's
// one-word promise, so while a run is in flight its progress is visible from
// EVERY tab, without spending a row of header on it. Quiet (the meta line's
// "N screened") covers the resting state.
//
// It wears the SCREENING hue (orange), not the brand green: the dashboard's own
// live chip (Requisitions.ScreeningCount) and the "Screened" lifecycle stage
// both read orange, so a green pill for the same event made one activity look
// like two. Green means COMPLETE in this palette (stages.ts) — a run still in
// flight is the one thing it must not say.
function LiveScreeningPill({ job }: { job: ScreenJob | null }) {
  if (job?.status !== "running") return null;
  const eta = job.eta_seconds != null && job.eta_seconds > 0
    ? (job.eta_seconds < 75 ? " · under a min left" : ` · ~${Math.round(job.eta_seconds / 60)} min left`)
    : "";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, background: "var(--hue-orange-tint)", borderRadius: 999, padding: "5px 12px", whiteSpace: "nowrap" }}>
      <span className="brss-pulse" aria-hidden="true" style={{ width: 8, height: 8, borderRadius: 999, background: "var(--hue-orange-solid)" }} />
      <span style={{ fontSize: 12.5, fontWeight: 700, color: "var(--hue-orange-ink)", fontVariantNumeric: "tabular-nums" }}>
        Screening {job.total ? `${job.done}/${job.total}` : "…"}{eta}
      </span>
    </span>
  );
}

export default function Workspace() {
  const { reqId = "" } = useParams();
  const nav = useNavigate();
  const { pathname } = useLocation();
  const me = useMe();
  const isHM = isHmActive(me);
  // Read-only workspace (2026-07-21): a reqs_read viewer with no recruiter/HM cap
  // (Legal, arriving from the audit trail) can look at everything but change nothing.
  const caps = me?.capabilities ?? [];
  const readOnly = caps.includes("reqs_read") && !caps.includes("recruiter") && !caps.includes("hm_take");
  const { data: role, error, loading, reload } = useFetch<RoleDetail>(`/api/roles/${reqId}`);
  const screenJob = useReqScreen(reqId, reload);
  const [blind, setBlind] = useState(false);
  const [askOpen, setAskOpen] = useState(false); // P44 Ask-Claude panel
  // Off unless the ask_claude_handoff flag is on (2026-10-23): the copy carries
  // candidates' names and the AI's read to Claude, which waits on InfoSec. A flag
  // still loading reads as off, so the button never flashes on.
  const askClaudeOn = useFeatureFlag("ask_claude_handoff") === true;
  const [staleGate, setStaleGate] = useState(false); // the "ground shifted" confirm
  const [drawerWidth, setDrawerWidth] = useState<number | null>(null); // review pane width
  const people = usePeople();
  const confirmLeave = useConfirmLeave(); // a dirty checklist gets to object first

  // The drawer width is a per-req preference — reset it when the req changes so a
  // wide pane in one workspace never leaks into the next.
  useEffect(() => { setDrawerWidth(null); }, [reqId]);

  // /reqs/:reqId[/:tab] — the third segment picks the tab, default overview.
  const seg = pathname.split("/").filter(Boolean)[2] ?? "overview";
  const active = TABS.some((t) => t.key === seg) ? seg : "overview";

  const closed = role?.lifecycle.status === "closed";

  // Opening a closed req is itself part of the record (audit answers "who
  // looked at this after it closed?"). Best-effort — never blocks the page.
  useEffect(() => {
    if (closed) {
      post(`/api/roles/${reqId}/events`, { action: "req_history_view" }).catch(() => {});
    }
  }, [closed, reqId]);

  // Stale-while-revalidate: only the FIRST load (no role yet) shows the
  // full-page spinner or error. A background reload (after a decision, publish,
  // etc.) keeps the workspace mounted — no full-page "postback" flash, no lost
  // drawer/selection/scroll — because useFetch retains the last-good role.
  if (!role) {
    if (loading) return <HeaderSkeleton />;
    return <Page max={900}><PageError message={error || "Requisition not found."} /></Page>;
  }

  const chip = STAGE_CHIP[role.stage] ?? STAGE_CHIP.draft_checklist;
  const activeHue = TABS.find((t) => t.key === active)?.hue ?? "";
  const openStaleGate = () => setStaleGate(true);
  const ctx: WorkspaceContext = { role, blind, setBlind, reload, drawerWidth, setDrawerWidth, openStaleGate, readOnly, closed };
  const stale = role.staleness;

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", position: "relative" }}>
      <div style={{ flex: "0 0 auto", padding: "12px 32px 0", background: "var(--white)", borderBottom: "1px solid var(--border-subtle)" }}>
        <BackButton onClick={() => confirmLeave(() => nav("/reqs"))} label="All requisitions" />
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", margin: "4px 0 10px" }}>
          <h1 style={{ fontSize: 22, letterSpacing: "-0.015em", margin: 0, lineHeight: 1.25 }}>{role.title}</h1>
          <span style={{ fontSize: 12, fontWeight: 700, padding: "4px 11px", borderRadius: 999, background: chip.bg, color: chip.fg }}>
            {chip.label}
          </span>
          {/* the design's stale chip: visible from every tab, opens the "what
              changed?" confirm (decided scores ran under older rules) */}
          {stale?.mode === "locked" && (
            <button
              onClick={openStaleGate}
              title={stale.summary}
              style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11.5, fontWeight: 700, padding: "3px 10px", borderRadius: 999, background: "var(--surface-warn)", color: "var(--warn-ink)", cursor: "pointer", whiteSpace: "nowrap", border: 0, fontFamily: "inherit" }}
            >
              ⏱ Scores stale · <span style={{ textDecoration: "underline", textUnderlineOffset: 2 }}>what changed?</span>
            </button>
          )}
          <span style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 12 }}>
            <LiveScreeningPill job={screenJob} />
            {active === "review" && (
              <>
                <Switch label="Blind review" checked={blind} onChange={setBlind} />
                {/* Every line of the bundle is the AI's read on a candidate, so the
                    button is for verdict_view holders only (InfoSec, 2026-10-14) — and
                    it covers every candidate screened, so not for a hiring manager,
                    who sees the ones shared with them (2026-10-21). */}
                {askClaudeOn && canSeeVerdict(me) && !isHmActive(me) && (
                  <Button size="sm" variant="secondary" onClick={() => setAskOpen(true)}>
                    <IconSparkle size={15} /> Ask Claude
                  </Button>
                )}
              </>
            )}
          </span>
        </div>

        {/* The facts row: the requisition's standing details, quiet by design
            (2026-09-07). They were tinted pills competing with the stage chip for
            attention — the chip says what to DO about this requisition, the facts only
            say what it IS, so they are now outlined and a size smaller. The
            jurisdiction chip is gone: the pack is on Overview, where its obligations
            are actionable. */}
        <div className="ws-facts">
          <span className="fact" title={`Job location — ${role.location_display}`}>
            <IconPin size={12} />
            {role.work_location?.place || role.location_display}
          </span>
          {role.req_code && <span className="fact fact-mono" title="iCIMS Req ID">{role.req_code}</span>}
          <span className={"fact" + (role.rubric_version ? "" : " fact-attn")} title={role.rubric_version ? `${role.requirement_count} requirements` : "Draft and publish the screening checklist first"}>
            <IconChecklist size={12} />
            {role.rubric_version ? `Checklist v${role.rubric_version} · ${role.requirement_count}` : "No checklist yet"}
          </span>
          <span className="fact">
            <IconFile size={12} />
            {role.screened
              ? `${role.candidate_count} screened`
              : role.batch_count ? `${role.batch_count} résumé${role.batch_count === 1 ? "" : "s"} · not screened` : "No résumés yet"}
          </span>
          <span className="fact-person">
            Added by <Person email={role.created_by} people={people} me={me?.user} />
          </span>
        </div>

        {readOnly && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 14px", marginBottom: 12, background: "var(--accent-blue-tint)", border: "1px solid var(--border-subtle)", borderRadius: 10 }}>
            <span style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>
              🔍 <strong style={{ color: "var(--ink)" }}>Read-only view</strong> — you can see everything on this
              requisition for audit, but decisions, screening and edits stay with the recruiting team.
            </span>
          </div>
        )}

        {closed && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 14px", marginBottom: 12, background: "var(--neutral-150)", border: "1px solid var(--border-subtle)", borderRadius: 10 }}>
            <span style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>
              <strong style={{ color: "var(--ink)" }}>This requisition is closed{outcomeSummary(role.lifecycle.outcome)}</strong>
              {" "}— screening, decisions and sharing are frozen; comments and notes stay open. The full record is on the Audit tab; the iCIMS requisition is unchanged.
            </span>
          </div>
        )}

        <Tabs
          active={active}
          onSelect={(k) => confirmLeave(() => nav(`/reqs/${reqId}/${k}`))}
          tabs={TABS.map((t) => {
            // The batch (uploads, screening) belongs to the recruiter — a hiring
            // manager's Candidates tab is disabled with a plain reason (2026-07-19).
            const tip = (isHM && t.key === "candidates")
              ? "The recruiter manages résumés and screening — your work lives in Checklist and Review."
              : t.disabled?.(role);
            return { key: t.key, label: t.label, hue: t.hue, count: t.count?.(role), disabled: !!tip, tooltip: tip };
          })}
        />
      </div>

      {/* The canvas takes a faint wash of the open tab's hue (app.css .ws-hue-*). */}
      <div className={"ws-canvas" + (activeHue ? ` ws-hue-${activeHue}` : "")} style={{ flex: "1 1 auto", minHeight: 0, overflow: "hidden" }}>
        {/* Keyed on the requisition: a deep link from one req straight to another
            remounts the open tab, so per-req state (the Review selection, the
            Candidates batch) can be read once on mount instead of chased in effects. */}
        <Outlet key={reqId} context={ctx} />
      </div>

      {askOpen && (
        <AskClaudePanel
          reqId={reqId}
          blind={blind}
          checklistVersion={role.rubric_version}
          onClose={() => setAskOpen(false)}
        />
      )}

      {/* the design's stale-gate confirm: "the ground shifted" — proceeding
          primes the Candidates tab; the re-screen is the ordinary screen click */}
      {staleGate && stale && (
        <ConfirmModal
          spec={{
            title: "The ground shifted under these scores",
            text: stale.summary,
            reason: "What changed: "
              + (stale.changed.length
                ? stale.changed.map((a) => ({ checklist: "the checklist", rules: "the hiring rules", location: "the job location", scoring_model: "the scoring model" }[a] ?? a)).join(", ")
                : "the published configuration")
              + ". Every scoring factor was checked against this run's stamps.",
            question: "Decisions are paused so old numbers never mix with new judgments. Re-screening resets this requisition to Screen; every recorded decision stays in the audit trail.",
            cta: `Re-screen ${role.candidate_count} candidate${role.candidate_count === 1 ? "" : "s"}`,
            onProceed: () => {
              setStaleGate(false);
              nav(`/reqs/${reqId}/candidates`);
            },
          }}
          onCancel={() => setStaleGate(false)}
        />
      )}
    </div>
  );
}
