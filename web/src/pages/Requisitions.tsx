// The requisition-centric home (P32, Addendum 11 Part A1): a DENSE sortable
// table over every requisition — saved-view pills, clickable metric cards,
// suggested-close banner rows, and the History view with outcome + retention.
//
// 2026-09-06 (pilot feedback): ONE ROW = ONE CLICK. A row opens its workspace
// Overview — no per-cell links, no Action column with a different destination per
// stage (the workspace's own next-step card carries that). The list is about
// REQUISITIONS: the counters are requisition counts and say so; the person's own
// screened total is a quiet readout, not a hero. The region pills went (the
// sidebar's region lens already is the region filter, and "IN" is its own region,
// not APAC); "Added" is a column and the default sort, newest first.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Page } from "../layout/AppShell";
import { Button, PageError, Chip, Callout, TableSkeleton } from "../components/ui";
import { IconSearch, IconPlus, IconPin, IconClock } from "../components/icons";
import HiringRules from "../components/HiringRules";
import { useMe } from "../MeContext";
import { isHmActive, isHmPortalDark } from "../hm";
import { useRegionLens } from "../RegionContext";
import { jdLanguageBadge } from "../lib/languages";
import { takeFresh, useFetch, usePolling, useFeatureFlag } from "../hooks";
import { STAGE_CHIP } from "../stages";
import { Person, personName, usePeople, type PeopleIndex } from "../people";
import { get, post, getPolled } from "../api";
import { ConfirmModal, OverlayRoot } from "../components/overlays";
import BatchQualityChip from "../components/BatchQualityChip";
import type { ClonePrefill, FeatureFlag, RoleCard, RolesResponse, ScreenJob } from "../types";

// Resolve a feature flag for a requisition's region: env-controlled flags read the
// resolved global value; normal flags use the region override if present, else the
// default. Mirrors feature_flags.flag_enabled on the server.
type FlagResolver = (key: string, region: string) => boolean;

const STAGE_ORDER = ["draft_checklist", "checklist_approved", "candidates_added", "screened", "in_review", "complete"];

type View = "my" | "active" | "needs_action" | "awaiting_hm" | "suggested" | "all" | "history";
type SortKey = "title" | "location" | "stage" | "candidates" | "hm" | "recruiter" | "added" | "activity" | "closed";

const VIEWER_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

/** "25 Aug" for this year, "25 Aug 2025" for any other — the way consumer apps stamp
 *  activity. The full timestamp stays on hover (see the cell). */
function fmtDay(ts: string, tz?: string): string {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, {
    day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }),
    ...(tz ? { timeZone: tz } : {}),
  });
}

/** The unabridged timestamp, for tooltips. */
function fmtWhenFull(ts: string, tz?: string): string {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short", ...(tz ? { timeZone: tz } : {}) });
}

function needsAction(r: RoleCard): boolean {
  return r.stage === "screened" || r.stage === "in_review";
}

function candidatesOf(r: RoleCard): number {
  return r.screened ? r.candidate_count : r.batch_count;
}

// Poll every in-flight screen across ALL reqs (one request for the whole list) so
// the Candidates column can show live "Screening N/M" while the user is on the
// dashboard or busy on another req. When a req drops out of the running set its
// results just landed: the list reloads AND that row blinks once (a momentary
// highlight, never persistent) so the completion is visible at a glance.
function useActiveScreens(reload: () => void, enabled: boolean): {
  active: Record<string, ScreenJob>; finished: Set<string>;
} {
  const [active, setActive] = useState<Record<string, ScreenJob>>({});
  const [finished, setFinished] = useState<Set<string>>(new Set());
  const running = useRef<Set<string>>(new Set());
  // Fast only while a run is live (or one was just started), slow otherwise, and
  // paused while the tab is hidden — see usePolling. The feed is recruiter-gated,
  // so a non-recruiter never polls a 403.
  usePolling(async () => {
    // The shell's bootstrap already fetched this for the first paint; only the
    // polls that follow go to the server (2026-09-10).
    const seeded = takeFresh<{ jobs: ScreenJob[] }>("/api/screen/active");
    const r = seeded ?? await getPolled<{ jobs: ScreenJob[] }>("/api/screen/active");
    const map: Record<string, ScreenJob> = {};
    for (const j of r.jobs) if (j.status === "running") map[j.req_id] = j;
    const now = new Set(Object.keys(map));
    const gone = [...running.current].filter((id) => !now.has(id));
    running.current = now;
    setActive(map);
    if (gone.length) {
      reload(); // a batch completed → refresh its count/stage
      setFinished((prev) => new Set([...prev, ...gone]));
      window.setTimeout(() => {
        setFinished((prev) => {
          const next = new Set(prev);
          gone.forEach((id) => next.delete(id));
          return next;
        });
      }, 2800);
    }
    return now.size > 0;
  }, enabled, [reload]);
  return { active, finished };
}

function retentionText(r: RoleCard): string {
  const ret = r.retention;
  if (!ret) return "—";
  if (ret.purged) return `PII purged ${ret.purge_by ?? ""} · audit retained`;
  if (ret.purge_by) {
    const days = Math.max(0, Math.ceil((new Date(ret.purge_by).getTime() - Date.now()) / 86_400_000));
    return `PII purges in ${days}d · audit kept 3y`;
  }
  return "—";
}

function StageChip({ stage }: { stage: string }) {
  const c = STAGE_CHIP[stage] ?? STAGE_CHIP.draft_checklist;
  // Keeps the per-stage palette from stages.ts but inherits the chip contract
  // (single line, capped width, ellipsis) so a long stage label stops widening the
  // Stage column — one of the cells that pushed the table past the viewport.
  return (
    <Chip style={{ background: c.bg, color: c.fg, fontSize: 12, height: 24 }}>{c.label}</Chip>
  );
}

export default function Requisitions() {
  const nav = useNavigate();
  const me = useMe();
  const isHM = isHmActive(me); // hiring-manager list variant (assigned reqs only)
  const { lens, ready: lensReady } = useRegionLens();
  // The region lens narrows the server-side list (the sidebar picker is THE region
  // filter; the old APAC/EMEA/Americas pills duplicated it — and "IN" is its own
  // region, so an India req never counted as APAC anyway).
  const rolesPath = lens ? `/api/roles?region=${encodeURIComponent(lens)}` : "/api/roles";
  // Held until identity lands, so the FIRST fetch already carries this person's
  // default region (2026-09-12). Without the gate the list loads unfiltered and
  // is then refetched, which reads as a flash of other regions' requisitions.
  const { data, error, loading, reload } = useFetch<RolesResponse>(lensReady ? rolesPath : null);
  const { active: activeScreens, finished: justFinished } =
    useActiveScreens(reload, !!me?.capabilities?.includes("recruiter"));
  const { data: featData } = useFetch<{ features: FeatureFlag[] }>("/api/features");
  const flags = useMemo(() => featData?.features ?? [], [featData]);
  const flagOn = useCallback<FlagResolver>((key, region) => {
    const f = flags.find((x) => x.key === key);
    if (!f) return false;
    if (f.control === "env") return f.enabled;
    return region in f.regions ? f.regions[region] : f.default;
  }, [flags]);
  // The "with the hiring manager" counter only means something while the HM portal
  // is on — its status values come from the portal flow. Off, it would sit at 0
  // forever, which is a card that teaches people to ignore cards.
  const hmPortalOn = useFeatureFlag("hm_portal") ?? false;
  const people = usePeople();
  // Persist the dashboard filters for the session (2026-07-20) — they used to
  // reset on every visit. Session-scoped so a fresh sign-in starts clean.
  const persisted = useRef<{ query?: string; view?: View; sort?: { key: SortKey; dir: 1 | -1 } } | null>(
    (() => { try { return JSON.parse(sessionStorage.getItem("rs.reqFilters") || "null"); } catch { return null; } })(),
  );
  const [query, setQuery] = useState(persisted.current?.query ?? "");
  const [view, setView] = useState<View>(persisted.current?.view ?? "my");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>(
    persisted.current?.sort ?? { key: "added", dir: -1 });
  useEffect(() => {
    try { sessionStorage.setItem("rs.reqFilters", JSON.stringify({ query, view, sort })); } catch { /* private mode */ }
  }, [query, view, sort]);
  const [decisionsFor, setDecisionsFor] = useState<RoleCard | null>(null); // the recorded-decisions popup

  const roles = useMemo(() => data?.roles ?? [], [data]);
  const open = useMemo(() => roles.filter((r) => r.lifecycle.status === "active"), [roles]);
  const history = useMemo(() => roles.filter((r) => r.lifecycle.status === "closed"), [roles]);
  // An HM's list is already server-scoped to their assigned reqs, so "My reqs"
  // means everything returned; a recruiter's means the ones they created.
  const myCount = isHM ? open.length : open.filter((r) => r.created_by === me?.user).length;

  // An all-reqs viewer (HR Head / SuperAdmin) who created nothing themselves
  // shouldn't land on an empty "My reqs" — default them to All once, on load.
  // Don't re-seed the default view when a persisted view was restored.
  const viewSeeded = useRef(!!persisted.current?.view);
  useEffect(() => {
    if (viewSeeded.current || !data) return;
    viewSeeded.current = true;
    if (!isHM && myCount === 0 && open.length > 0) setView("all");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);
  const suggestedCount = open.filter((r) => r.suggest_close.suggest).length;

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const dataset = view === "history" ? history : open;
    const inView = (r: RoleCard): boolean => {
      switch (view) {
        case "my": return isHM ? true : r.created_by === me?.user;
        case "needs_action": return needsAction(r);
        case "awaiting_hm": return !!r.hm?.status; // invited / review requested
        case "suggested": return r.suggest_close.suggest;
        default: return true; // all · active · history (dataset already scoped)
      }
    };
    const matches = (r: RoleCard): boolean =>
      !q ||
      r.title.toLowerCase().includes(q) ||
      (r.req_code ?? "").toLowerCase().includes(q) ||
      r.location_display.toLowerCase().includes(q) ||
      (r.work_location?.place ?? "").toLowerCase().includes(q) ||
      // Search by hiring manager too (2026-07-19) — name or email.
      (r.hm?.name ?? "").toLowerCase().includes(q) ||
      (r.hm?.email ?? "").toLowerCase().includes(q) ||
      // ...and by the RECRUITER who owns it (2026-07-20) — resolved name or email.
      personName(r.created_by, people).toLowerCase().includes(q) ||
      (r.created_by ?? "").toLowerCase().includes(q);
    const value = (r: RoleCard): string | number => {
      switch (sort.key) {
        case "title": return r.title.toLowerCase();
        case "location": return (r.work_location?.place || r.location_display).toLowerCase();
        case "stage": return STAGE_ORDER.indexOf(r.stage);
        case "candidates": return candidatesOf(r);
        // HM name (falls back to email); no HM sorts to the end of an A–Z sort.
        case "hm": return (r.hm?.name || r.hm?.email || "￿").toLowerCase();
        case "recruiter": return (personName(r.created_by, people) || "￿").toLowerCase();
        case "added": return r.lifecycle.created_at ?? "";
        case "activity": return r.last_activity_utc ?? "";
        case "closed": return r.lifecycle.closed_at ?? "";
      }
    };
    return dataset.filter((r) => inView(r) && matches(r)).sort((a, b) => {
      const va = value(a), vb = value(b);
      return (va < vb ? -1 : va > vb ? 1 : 0) * sort.dir;
    });
  }, [open, history, view, query, sort, me, isHM, people]);

  // HM signed in with the portal flag off: role known, hm_take suppressed. Show a
  // plain notice instead of a broken shell (P42) — "My preferences" nav still shows.
  if (isHmPortalDark(me)) {
    return (
      <Page max={620}>
        <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, padding: "36px 40px", textAlign: "center", marginTop: 40 }}>
          <h1 style={{ fontSize: 22, marginBottom: 10 }}>The hiring-manager portal isn't enabled yet</h1>
          <p style={{ fontSize: 14, color: "var(--ink-soft)", margin: 0 }}>
            Your account is set up as a hiring manager, but the portal is turned off for now — ask IT to enable it.
          </p>
        </div>
      </Page>
    );
  }

  // The shape of the table, not a spinner: the first paint keeps the layout still
  // and the rows land in place (revisits come from the SWR cache and skip this).
  // "Not ready" is a kind of loading: the roles fetch is held until identity
  // lands so the FIRST one already carries this person's default region, and the
  // skeleton is what covers that wait — a spinner-free first paint either way.
  if (loading || !lensReady || !data) return <Page><TableSkeleton rows={7} /></Page>;
  if (error) return <Page><PageError message={error} /></Page>;

  const m = data!.metrics;
  const myScreened = m.my_candidates_screened ?? 0;
  const onSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 }
      : { key, dir: key === "added" || key === "activity" || key === "closed" ? -1 : 1 }));
  // A metric card is a toggle: clicking the active one returns to the plain list.
  const toggleView = (v: View) => setView((cur) => (cur === v ? "all" : v));

  const pills: { key: View; label: string }[] = [
    { key: "my", label: `My reqs · ${myCount}` },
    { key: "all", label: `All · ${open.length}` },
  ];
  const liveJobs = Object.values(activeScreens);
  const liveTotal = liveJobs.reduce((s, j) => s + j.total, 0);
  const liveDone = liveJobs.reduce((s, j) => s + j.done, 0);
  const cardView = view === "active" || view === "needs_action" || view === "awaiting_hm" || view === "suggested";
  const CARD_LABEL: Record<string, string> = {
    active: "Active", needs_action: "Need your action", awaiting_hm: "With the hiring manager", suggested: "Suggested to close",
  };

  return (
    <Page max={1240}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 20, flexWrap: "wrap", marginBottom: 18 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 16, flexWrap: "wrap" }}>
          <h1 style={{ fontSize: 28, letterSpacing: "-0.01em", margin: 0 }}>Requisitions</h1>
          {/* The person's own tally — a readout, not a hero: this page is about
              requisitions, and "how much have I screened" is a side glance. */}
          {!isHM && myScreened > 0 && (
            <span style={{ fontSize: 13, color: "var(--ink-soft)" }} title="Résumés screened on the requisitions you created">
              You've screened <strong style={{ color: "var(--ink)", fontVariantNumeric: "tabular-nums" }}>{myScreened}</strong> résumé{myScreened === 1 ? "" : "s"}
            </span>
          )}
          {liveJobs.length > 0 && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8, background: "var(--hue-orange-tint)", borderRadius: 999, padding: "4px 11px", whiteSpace: "nowrap" }}>
              <span className="brss-pulse" aria-hidden="true" style={{ width: 7, height: 7, borderRadius: 999, background: "var(--hue-orange-solid)" }} />
              <span style={{ fontSize: 12.5, fontWeight: 700, color: "var(--hue-orange-ink)" }}>{liveJobs.length} screening now</span>
              {liveTotal > 0 && <span style={{ fontSize: 12, color: "var(--ink-soft)", fontVariantNumeric: "tabular-nums" }}>· {liveDone}/{liveTotal}</span>}
            </span>
          )}
        </div>
        {!isHM && <Button onClick={() => nav("/reqs/new")}><IconPlus size={16} /> New requisition</Button>}
      </div>

      {/* An UNSCOPED account (no region grants) is now unrestricted rather than
          locked out — see service.region_context. That is the right default while
          nothing can grant a region, but it must not be silent, so admins are told.
          Recruiters are not: nothing is hidden from them, so there is nothing for
          them to act on. */}
      {me?.has_admin && me?.regions?.ungranted && (
        <div style={{ marginBottom: 18 }}>
          <Callout variant="warn" title="Region access isn't scoped for this account">
            No regions are assigned to <strong>{me.user}</strong>, so every region is
            visible. That is deliberate while there is no way to grant regions — but it
            means the region lens isn't filtering anything for you.
          </Callout>
        </div>
      )}

      {/* Requisition counters — each one a filter. Labels say what they count so
          nobody reads them as candidate numbers (they used to sit beside a
          candidates total, which is exactly how they were read). */}
      {!isHM && (
        <div className="summary-row">
          <MetricCard hue="green" value={open.length} label="Active requisitions" active={view === "active"} onClick={() => toggleView("active")} />
          <MetricCard hue="amber" value={m.need_action} label="Need your action" sub="screened, awaiting decisions" active={view === "needs_action"} onClick={() => toggleView("needs_action")} />
          {hmPortalOn && (
            <MetricCard hue="violet" value={m.awaiting_hm} label="With the hiring manager" sub="invited or asked to review" active={view === "awaiting_hm"} onClick={() => toggleView("awaiting_hm")} />
          )}
          <MetricCard hue="slate" value={suggestedCount} label="Suggested to close" sub="all decided, quiet for 14 days" active={view === "suggested"} onClick={() => toggleView("suggested")} />
        </div>
      )}

      {/* search + saved-view pills */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
        <div style={{ position: "relative", flex: "1 1 260px", minWidth: 220 }}>
          <span style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--ink-muted)", display: "inline-flex" }}>
            <IconSearch size={16} />
          </span>
          <input className="input" style={{ paddingLeft: 36 }} placeholder={isHM ? "Search title, req ID, place or recruiter…" : "Search title, req ID, place or hiring manager…"} value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          {pills.map((p) => (
            <button key={p.key} onClick={() => setView(p.key)} className={"pill" + (view === p.key ? " active" : "")}>{p.label}</button>
          ))}
          {cardView && (
            <button onClick={() => setView("all")} className="pill active" title="Clear this filter">
              {CARD_LABEL[view]} ✕
            </button>
          )}
          <span style={{ width: 1, height: 20, background: "var(--border-strong)", margin: "0 4px" }} />
          <button onClick={() => setView("history")} className={"pill" + (view === "history" ? " active" : "")}>
            History · {history.length}
          </button>
        </div>
      </div>

      {/* the dense table — one row, one click, one destination: the workspace */}
      <div className="table-wrap" style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 14 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border-subtle)" }}>
              <Th label="Requisition" k="title" sort={sort} onSort={onSort} />
              <Th label="Location" k="location" sort={sort} onSort={onSort} className="col-secondary" />
              {view !== "history" && <Th label="Stage" k="stage" sort={sort} onSort={onSort} />}
              {view !== "history" && <Th label="Candidates" k="candidates" sort={sort} onSort={onSort} />}
              {view !== "history" && (isHM
                ? <Th label="Recruiter" k="recruiter" sort={sort} onSort={onSort} className="col-optional" />
                : <Th label="Hiring manager" k="hm" sort={sort} onSort={onSort} className="col-optional" />)}
              {view !== "history" && <Th label="Added" k="added" sort={sort} onSort={onSort} />}
              {view === "history" && <Th label="Closed" k="closed" sort={sort} onSort={onSort} />}
              {view === "history" && <th style={TH_STYLE}>Outcome</th>}
              {view === "history" && <th style={TH_STYLE}>Data retention</th>}
              {view === "history" && <th style={{ ...TH_STYLE, textAlign: "right" }}>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} style={{ padding: "40px 20px", textAlign: "center", color: "var(--ink-soft)" }}>
                  {view === "my" ? "No requisitions created by you yet — try All, or create one." : "Nothing matches this view."}
                </td>
              </tr>
            )}
            {rows.map((r) =>
              view !== "history" && r.suggest_close.suggest ? (
                <SuggestionRow key={r.req_id} r={r} onDone={reload} onDecisions={setDecisionsFor} />
              ) : view === "history" ? (
                <HistoryRow key={r.req_id} r={r} flagOn={flagOn} onDone={reload} />
              ) : (
                <ActiveRow key={r.req_id} r={r} isHM={isHM} job={activeScreens[r.req_id]} people={people}
                  meEmail={me?.user} blink={justFinished.has(r.req_id)} onReload={reload} />
              )
            )}
          </tbody>
        </table>
      </div>
      {decisionsFor && <DecisionsPopup r={decisionsFor} onClose={() => setDecisionsFor(null)} />}
    </Page>
  );
}

// 11px horizontal padding (was 14): measured at 1280px, the table overflowed its box by
// 85px; shorter dates, one sort arrow and this padding bring it to fit exactly.
const TH_STYLE: React.CSSProperties = {
  textAlign: "left", padding: "11px 11px", fontSize: 11.5, fontWeight: 700,
  letterSpacing: "0.05em", textTransform: "uppercase", color: "var(--ink-soft)", whiteSpace: "nowrap",
};
const TD_STYLE: React.CSSProperties = { padding: "12px 11px", verticalAlign: "top" };

function Th({ label, k, sort, onSort, className }: {
  label: string; k: SortKey;
  sort: { key: SortKey; dir: 1 | -1 };
  onSort: (k: SortKey) => void;
  /** col-secondary / col-optional — see app.css. A header MUST carry the same
   *  class as its cells or the column shears off by one on narrow screens. */
  className?: string;
}) {
  const active = sort.key === k;
  return (
    <th className={className} style={TH_STYLE}>
      {/* The arrow marks the ACTIVE sort only (consumer tables do the same): five
          permanent "↓↑" glyphs were noise, and ~14px of width per header. */}
      <button onClick={() => onSort(k)} title={active ? undefined : `Sort by ${label.toLowerCase()}`}
              style={{ background: "none", border: 0, cursor: "pointer", font: "inherit", color: active ? "var(--ink)" : "inherit", padding: 0, display: "inline-flex", alignItems: "center", gap: 4, letterSpacing: "inherit", textTransform: "inherit" }}>
        {label}{active && <span aria-hidden="true">{sort.dir === 1 ? "↑" : "↓"}</span>}
      </button>
    </th>
  );
}

function MetricCard({ value, label, sub, active, onClick, hue = "green" }:
  { value: number; label: string; sub?: string; active: boolean; onClick: () => void;
    hue?: "green" | "violet" | "amber" | "slate" | "blue" }) {
  // One hue per measure (app.css .metric-hue-*): the number and a slim top accent
  // carry it, so the cards read as distinct things rather than one green block.
  return (
    <button
      onClick={onClick}
      className={`hero-hover metric-card metric-hue-${hue}${active ? " is-active" : ""}`}
      title={active ? "Click again to clear this filter" : "Click to filter the list"}
      aria-pressed={active}
      style={{
        textAlign: "left", background: "var(--white)", fontFamily: "inherit", cursor: "pointer",
        border: "1px solid var(--border-subtle)",
        borderRadius: 14, padding: "14px 18px 12px",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="metric-value" style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.1 }}>{value}</span>
        <span style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>{label}</span>
      </div>
      {sub && <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 3 }}>{sub}</div>}
    </button>
  );
}

function ReqCell({ r }: { r: RoleCard }) {
  // The title stays a real link (middle-click, copy address, screen readers); the
  // ROW is the click target for everyone else, so the link stops the row handler
  // from firing a second navigation to the same place.
  return (
    <td style={{ ...TD_STYLE, position: "relative", minWidth: 160 }}>
      <Link
        to={`/reqs/${r.req_id}`}
        onClick={(e) => e.stopPropagation()}
        style={{ fontWeight: 700, color: "var(--ink)", textDecoration: "none" }}
      >
        {r.title}
      </Link>
      {/* req code — ALWAYS monospace. Omitted entirely when a role has none: the
          old em-dash placeholder read as a stray hyphen dangling under the title. */}
      {r.req_code && (
        <div style={{ fontFamily: "monospace", fontSize: 11.5, color: "var(--ink-muted)", marginTop: 2 }}>{r.req_code}</div>
      )}
    </td>
  );
}

/** The design's recorded-decisions popup (the close-suggestion row's link). */
function DecisionsPopup({ r, onClose }: { r: RoleCard; onClose: () => void }) {
  const [rows, setRows] = useState<{ name: string; action: string; ts: string | null; reviewer: string | null }[] | null>(null);
  // Outside the requisition's team the server says so instead of listing (2026-10-21).
  const [hiddenNote, setHiddenNote] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    get<{ decisions: { name: string; action: string; ts: string | null; reviewer: string | null }[];
          hidden?: boolean; message?: string }>(`/api/roles/${r.req_id}/decisions`)
      .then((d) => { if (live) { setRows(d.decisions); setHiddenNote(d.hidden ? (d.message ?? "") : null); } })
      .catch(() => { if (live) setRows([]); });
    return () => { live = false; };
  }, [r.req_id]);
  const label: Record<string, { text: string; bg: string; fg: string }> = {
    forward: { text: "Move forward", bg: "var(--hue-green-tint)", fg: "var(--hue-green-ink)" },
    discuss: { text: "Discuss", bg: "var(--hue-amber-tint)", fg: "var(--hue-amber-ink)" },
    not: { text: "Not a fit", bg: "var(--hue-slate-tint)", fg: "var(--hue-slate-ink)" },
  };
  return (
    <OverlayRoot onDismiss={onClose} zIndex={60}>
      <div className="rise" role="dialog" aria-label={`Decisions recorded — ${r.title}`}
        style={{ position: "relative", width: "min(540px, 92%)", background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, boxShadow: "var(--shadow-lg)", padding: "20px 24px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>Decisions recorded — {r.title}</div>
          <button onClick={onClose} aria-label="Close decisions" style={{ width: 30, height: 30, borderRadius: "50%", border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink-soft)", cursor: "pointer", fontSize: 13 }}>✕</button>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 340, overflowY: "auto" }}>
          {rows == null && <div style={{ fontSize: 13, color: "var(--ink-soft)" }}>Loading…</div>}
          {hiddenNote != null && <div style={{ fontSize: 13, color: "var(--ink-soft)" }}>{hiddenNote}</div>}
          {hiddenNote == null && rows?.length === 0 && <div style={{ fontSize: 13, color: "var(--ink-soft)" }}>No decisions on record for the current run.</div>}
          {rows?.map((d, i) => {
            const s = label[d.action] ?? label.not;
            return (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", border: "1px solid var(--neutral-150)", borderRadius: 10 }}>
                <span style={{ fontSize: 13, fontWeight: 700, flex: "1 1 auto" }}>{d.name}</span>
                <span style={{ display: "inline-flex", alignItems: "center", fontSize: 11.5, fontWeight: 700, padding: "3px 10px", borderRadius: 999, background: s.bg, color: s.fg, whiteSpace: "nowrap" }}>{s.text}</span>
                <span style={{ fontSize: 11.5, color: "var(--ink-muted)", whiteSpace: "nowrap" }}>{d.ts ? d.ts.slice(0, 10) : ""}</span>
              </div>
            );
          })}
        </div>
        <div style={{ fontSize: 11.5, color: "var(--ink-muted)", marginTop: 12 }}>
          Reasons and evidence live in the requisition's Audit tab.
        </div>
      </div>
    </OverlayRoot>
  );
}

function LocationCell({ r }: { r: RoleCard }) {
  return (
    <td className="col-secondary" style={TD_STYLE}>
      <div className="chip-row" style={{ alignItems: "center", gap: 6 }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}
              title={r.work_location?.place ? `${r.work_location.place} — ${r.location_display}` : undefined}>
          <IconPin size={13} style={{ color: "var(--hue-blue-ink)", flex: "0 0 auto" }} />
          {/* The CITY the desk is in, which is what a recruiter scans this column
              for; the country only ever repeated what the law chip below already
              implies. Falls back to the jurisdiction when no place is on record. */}
          {r.work_location?.place || r.location_display}
        </span>
        {jdLanguageBadge(r.jd_language) && <Chip tone="info">{jdLanguageBadge(r.jd_language)}</Chip>}
      </div>
      {/* The law is something you can OPEN now (HiringRules): hover shows what the
          pack asks of the recruiter, in the pack's own words. */}
      <div style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11.5, color: "var(--ink-muted)", marginTop: 3 }}
           onClick={(e) => e.stopPropagation()}>
        <HiringRules code={r.job_location} law={r.law} quiet style={{ color: "var(--ink-muted)", fontSize: 11.5 }} />
      </div>
    </td>
  );
}

function ScreeningCount({ job }: { job: ScreenJob }) {
  // Live batch progress, styled apart from the plain count: warm orange + a
  // pulsing dot so a running screen reads at a glance across the whole list.
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 7, fontWeight: 700, color: "var(--hue-orange-ink)", whiteSpace: "nowrap" }}>
      <span className="brss-pulse" aria-hidden="true" style={{ flex: "0 0 auto", width: 7, height: 7, borderRadius: 999, background: "var(--hue-orange-solid)" }} />
      Screening {job.total ? `${job.done}/${job.total}` : "…"}
    </span>
  );
}

/** The stale badge + one-click refresh (2026-07-19): shown at EVERY stage with
 *  results — screened, in-review AND complete. The refresh icon fires the same
 *  re-screen the workspace offers (confirm first; recorded decisions stay in
 *  the audit trail; the live progress chip takes over via the active poll). */
function StaleRefresh({ r, locked, onStarted }: { r: RoleCard; locked?: boolean; onStarted: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!r.staleness) return null;
  const decided = r.reviewed_count > 0;

  async function rescreen() {
    setBusy(true);
    setErr(null);
    try {
      await post(`/api/roles/${r.req_id}/screen`);
      setConfirming(false);
      onStarted();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div style={{ marginTop: 4, display: "inline-flex", alignItems: "center", gap: 6 }} onClick={(e) => e.stopPropagation()}>
        <Chip tone="warn" title={r.staleness.summary} icon={<IconClock size={12} />}
              style={{ borderRadius: 6, height: 20, fontSize: 10.5 }}>
          Scores stale
        </Chip>
        <button
          onClick={() => setConfirming(true)}
          disabled={locked}
          aria-label={`Refresh scores — ${r.title}`}
          title={locked ? "Screening is in progress — wait for it to finish." : "Re-screen with the current rules"}
          style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 22, height: 22, borderRadius: 6, border: "1px solid var(--warn-border)", background: "var(--white)", color: "var(--warn-ink)", cursor: locked ? "default" : "pointer", opacity: locked ? 0.45 : 1, padding: 0 }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 12a9 9 0 1 1-2.64-6.36" />
            <path d="M21 3v6h-6" />
          </svg>
        </button>
      </div>
      {confirming && (
        <ConfirmModal
          spec={{
            title: "Re-screen with the current rules?",
            reason: r.staleness.summary,
            question: decided
              ? `Your ${r.reviewed_count} recorded decision${r.reviewed_count === 1 ? "" : "s"} stay in the audit trail — the new run starts the review fresh. Cached résumé readings are reused, so unchanged rules cost nothing to re-apply.`
              : "Cached résumé readings are reused, so unchanged rules cost nothing to re-apply.",
            cta: busy ? "Starting…" : "Refresh scores",
            onProceed: busy ? undefined : rescreen,
          }}
          onCancel={() => { if (!busy) { setConfirming(false); setErr(null); } }}
        />
      )}
      {err && <div style={{ fontSize: 11, color: "var(--warn-ink)", marginTop: 3 }}>{err}</div>}
    </>
  );
}

/** Row-level keyboard + click: Enter/Space or a click anywhere opens the workspace. */
function rowProps(nav: (to: string) => void, to: string, title: string) {
  return {
    tabIndex: 0,
    "aria-label": `Open ${title}`,
    onClick: () => nav(to),
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.target !== e.currentTarget) return; // a control inside handles its own keys
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); nav(to); }
    },
  };
}

function ActiveRow({ r, isHM, job, people, meEmail, blink = false, onReload }: {
  r: RoleCard; isHM: boolean; job?: ScreenJob; people: PeopleIndex; meEmail?: string | null;
  blink?: boolean; onReload: () => void;
}) {
  const nav = useNavigate();
  const count = candidatesOf(r);
  const localDiffers = r.last_activity_utc && r.tz && r.tz !== VIEWER_TZ;
  // The batch is locked by a run — live here (job) or paused (role payload).
  const locked = !!job || r.screening?.status === "paused";
  const added = r.lifecycle.created_at;
  const addedTip = [
    added ? `Added ${fmtWhenFull(added)}` : "",
    r.last_activity_utc ? `last activity ${fmtWhenFull(r.last_activity_utc)}`
      + (localDiffers ? ` (${fmtWhenFull(r.last_activity_utc, r.tz)} local time)` : "") : "",
  ].filter(Boolean).join(" · ");
  return (
    <tr {...rowProps(nav, `/reqs/${r.req_id}`, r.title)}
        className={"row-link" + (blink ? " row-blink" : "")}
        style={{ borderBottom: "1px solid var(--neutral-150)" }}>
      <ReqCell r={r} />
      <LocationCell r={r} />
      <td style={TD_STYLE}>
        <StageChip stage={r.stage} />
        {/* Scores produced under older rules — badge + refresh at ANY stage
            (the refresh waits while a run is live or paused). */}
        {!isHM && <StaleRefresh r={r} locked={locked} onStarted={onReload} />}
      </td>
      <td style={TD_STYLE}>
        {/* While a batch is screening, the count becomes a live progress readout. */}
        <span style={{ fontWeight: 700, color: "var(--ink)", fontVariantNumeric: "tabular-nums" }}>
          {job ? <ScreeningCount job={job} /> : count}
        </span>
        {!job && r.batch_quality && (
          <div style={{ marginTop: 4 }}>
            {/* band only on the dashboard — the score number lives in the workspace */}
            <BatchQualityChip bq={r.batch_quality} showScore={false} />
          </div>
        )}
      </td>
      {isHM ? (
        // HM list: the "Recruiter" column names the TA who owns the req + a note
        // while a review has been requested of this manager.
        <td className="col-optional" style={TD_STYLE}>
          <Person email={r.created_by} people={people} me={meEmail} />
          {r.hm?.status === "review_requested" && (
            <div style={{ fontSize: 11.5, fontWeight: 700, color: "var(--hue-violet-ink)", marginTop: 2 }}>requested your review</div>
          )}
        </td>
      ) : (
        <td className="col-optional" style={TD_STYLE}>
          {r.hm && (r.hm.name || r.hm.email) ? (
            <>
              <Person email={r.hm.email || r.hm.name} known={r.hm.name} people={people} />
              {/* status only when the portal flow set one — a name on record has none */}
              {r.hm.status && <div style={{ fontSize: 11.5, color: "var(--ink-muted)", marginTop: 2 }}>{r.hm.status.replace("_", " ")}</div>}
            </>
          ) : (
            <span style={{ color: "var(--ink-muted)" }}>—</span>
          )}
        </td>
      )}
      <td style={{ ...TD_STYLE, whiteSpace: "nowrap" }}>
        {added ? (
          <span title={addedTip}>{fmtDay(added)}</span>
        ) : (
          <span style={{ color: "var(--ink-muted)" }}>—</span>
        )}
      </td>
    </tr>
  );
}

// A requisition the app suggests closing — a human always confirms; "Keep open"
// snoozes the suggestion for 30 days and the row rejoins the list.
function SuggestionRow({ r, onDone, onDecisions }: { r: RoleCard; onDone: () => void; onDecisions: (r: RoleCard) => void }) {
  const [picking, setPicking] = useState(false);
  const [outcome, setOutcome] = useState<"hired" | "cancelled">("hired");
  const [hiredCount, setHiredCount] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onDone();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  const confirmClose = () => act(() => post(`/api/roles/${r.req_id}/close`,
    outcome === "hired" ? { outcome, hired_count: hiredCount } : { outcome }));
  const keepOpen = () => act(() => post(`/api/roles/${r.req_id}/snooze`, { days: 30 }));

  return (
    <tr style={{ borderBottom: "1px solid var(--neutral-150)" }}>
      {/* Amber, not green: this row ASKS for a decision (confirm the close), and in
          the semantic palette green means done while amber means attention. */}
      <td colSpan={7} style={{ padding: "14px 14px", background: "var(--hue-amber-tint)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
          <div style={{ flex: "1 1 320px", minWidth: 0 }}>
            <Link to={`/reqs/${r.req_id}`} style={{ fontWeight: 700, color: "var(--ink)" }}>{r.title}</Link>
            {r.req_code && <span style={{ fontFamily: "monospace", fontSize: 11.5, color: "var(--ink-muted)", marginLeft: 8 }}>{r.req_code}</span>}
            <div style={{ fontSize: 12.5, color: "var(--hue-amber-ink)", marginTop: 3 }}>
              <button onClick={() => onDecisions(r)} style={{ background: "none", border: 0, padding: 0, fontFamily: "inherit", fontSize: 12.5, fontWeight: 700, color: "var(--hue-amber-ink)", cursor: "pointer", textDecoration: "underline", textUnderlineOffset: 2 }}>
                All {r.suggest_close.decided} decisions recorded
              </button>
              , quiet for 14 days · closing here leaves iCIMS unchanged
            </div>
            {error && <div style={{ fontSize: 12.5, color: "var(--warn-ink)", marginTop: 3 }}>{error}</div>}
          </div>
          {!picking ? (
            <div style={{ display: "flex", gap: 8 }}>
              <Button size="sm" onClick={() => setPicking(true)} disabled={busy}>Confirm close</Button>
              <Button size="sm" variant="secondary" onClick={keepOpen} disabled={busy}>Keep open</Button>
            </div>
          ) : (
            <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, fontWeight: 700, cursor: "pointer" }}>
                <input type="radio" name={`outcome-${r.req_id}`} checked={outcome === "hired"} onChange={() => setOutcome("hired")} />
                Hired
              </label>
              {outcome === "hired" && (
                <input className="input" type="number" min={1} value={hiredCount} aria-label="Hired count"
                  onChange={(e) => setHiredCount(Math.max(1, Number(e.target.value) || 1))}
                  style={{ width: 64, height: 32 }} />
              )}
              <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, fontWeight: 700, cursor: "pointer" }}>
                <input type="radio" name={`outcome-${r.req_id}`} checked={outcome === "cancelled"} onChange={() => setOutcome("cancelled")} />
                No hire
              </label>
              <Button size="sm" onClick={confirmClose} disabled={busy}>Close requisition</Button>
              <Button size="sm" variant="ghost" onClick={() => setPicking(false)} disabled={busy}>Cancel</Button>
            </div>
          )}
        </div>
      </td>
    </tr>
  );
}

const FLAG_OFF_TIP = "Turned off — ask IT (feature flag)";

function HistoryRow({ r, flagOn, onDone }: { r: RoleCard; flagOn: FlagResolver; onDone: () => void }) {
  const nav = useNavigate();
  const out = r.lifecycle.outcome;
  const [busy, setBusy] = useState<"reopen" | "clone" | null>(null);
  const reopenOn = flagOn("icims_reopen", r.region);
  const cloneOn = flagOn("icims_clone", r.region);
  const syncOn = flagOn("icims_close_sync", r.region);

  async function reopen() {
    setBusy("reopen");
    try {
      await post(`/api/roles/${r.req_id}/reopen`);
      onDone();
    } catch {
      setBusy(null);
    }
  }

  async function clone() {
    setBusy("clone");
    try {
      const prefill = await post<ClonePrefill>(`/api/roles/${r.req_id}/clone`);
      nav("/reqs/new", { state: { prefill } });
    } catch {
      setBusy(null);
    }
  }

  return (
    <tr {...rowProps(nav, `/reqs/${r.req_id}`, r.title)} className="row-link" style={{ borderBottom: "1px solid var(--neutral-150)" }}>
      <ReqCell r={r} />
      <LocationCell r={r} />
      <td style={{ ...TD_STYLE, whiteSpace: "nowrap" }}>
        {r.lifecycle.closed_at ? new Date(r.lifecycle.closed_at).toLocaleDateString(undefined, { dateStyle: "medium" }) : "—"}
      </td>
      <td style={TD_STYLE}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap" }} onClick={(e) => e.stopPropagation()}>
          {out?.kind === "hired" ? (
            <Link to={`/reqs/${r.req_id}/audit`} style={{ display: "inline-flex", fontSize: 12, fontWeight: 700, padding: "4px 10px", borderRadius: 999, background: "var(--hue-blue-tint)", color: "var(--hue-blue-ink)", whiteSpace: "nowrap" }}>
              Hired · {out.hired_count ?? 1}
            </Link>
          ) : (
            <span style={{ display: "inline-flex", fontSize: 12, fontWeight: 700, padding: "4px 10px", borderRadius: 999, background: "var(--neutral-150)", color: "var(--ink-soft)", whiteSpace: "nowrap" }}>
              No hire — cancelled
            </span>
          )}
          {syncOn && (
            <span title="Close status reflected to iCIMS" style={{ display: "inline-flex", fontSize: 11, fontWeight: 700, padding: "3px 8px", borderRadius: 999, background: "var(--neutral-100)", color: "var(--ink-soft)", border: "1px solid var(--border-subtle)", whiteSpace: "nowrap" }}>
              Synced to iCIMS
            </span>
          )}
        </span>
      </td>
      <td style={{ ...TD_STYLE, fontSize: 12.5, color: "var(--ink-soft)", whiteSpace: "nowrap" }}>{retentionText(r)}</td>
      <td style={{ ...TD_STYLE, textAlign: "right", whiteSpace: "nowrap" }} onClick={(e) => e.stopPropagation()}>
        {/* History keeps its buttons: reopen and clone have no other home. */}
        <Button size="sm" variant="secondary" onClick={() => nav(`/audit?req=${r.req_id}`)}>View audit</Button>
        <Button size="sm" variant="secondary" style={{ marginLeft: 8 }}
          disabled={!reopenOn || busy !== null} title={reopenOn ? "Move back to active" : FLAG_OFF_TIP}
          onClick={reopen}>Reopen</Button>
        <Button size="sm" variant="secondary" style={{ marginLeft: 8 }}
          disabled={!cloneOn || busy !== null} title={cloneOn ? "Start a new requisition from this one" : FLAG_OFF_TIP}
          onClick={clone}>Clone</Button>
      </td>
    </tr>
  );
}
