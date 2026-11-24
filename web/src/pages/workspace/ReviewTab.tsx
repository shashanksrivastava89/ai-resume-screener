// The workspace's Review tab: ranked shortlist, filters, decisions and the
// candidate drawer (moved from the pre-v2 Review page). Blind review lives in
// this tab's filter row (state still in the workspace context so the drawer
// shares it) — it's only meaningful while reviewing.
//
// Hidden keyboard shortcuts (an accessibility affordance, no visible chrome):
//   j / k       move the active row down / up (visible focus ring)
//   enter / o   open the active row's drawer
//   1 / 2 / 3   Move forward / Discuss / Not a fit on the drawer's candidate
//               (when open), else the active row
//   esc         close the drawer, else clear the selection
// Inert while focus is in an input/textarea/select or contentEditable.
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Badge, Button, Tip, InfoDot, CenterLoading, PageError } from "../../components/ui";
import { IconSearch, IconChevronRight, IconSparkle, IconComment, IconOutdated, IconPin, IconX } from "../../components/icons";
import { loadReviewState, saveReviewState } from "../../reviewState";
import LocationRadius from "../../components/LocationRadius";
import { formatRadius, unitsFor } from "../../lib/units";
import CandidateDrawer from "../ReviewDrawer";
import BatchQualityChip from "../../components/BatchQualityChip";
import HmTakeButtons, { TAKE_LABEL, type Take } from "../../components/HmTakeButtons";
import { ConfirmModal, OverlayRoot, useToast } from "../../components/overlays";
import { get, post, put } from "../../api";
import { invalidate } from "../../hooks";
import { useMe } from "../../MeContext";
import { isHmActive } from "../../hm";
import { hasVerdict } from "../../verdict";
import { usePersonName } from "../../people";
import { useWorkspace, tabPath } from "./context";
import type { BatchDecisionResponse, DecisionResponse, ReviewResponse, ReviewCard, ScoreMathBrief } from "../../types";

// The TA-side chip that surfaces the assigned HM's take beside a candidate's band.
export function HmTakeChip({ take, name }: { take: Take; name?: string }) {
  // Matches HmTakeButtons: strong yes = green, yes = blue, no = slate.
  const bg = take === "no" ? "var(--hue-slate-tint)" : take === "yes" ? "var(--hue-blue-tint)" : "var(--hue-green-tint)";
  const fg = take === "no" ? "var(--hue-slate-ink)" : take === "yes" ? "var(--hue-blue-ink)" : "var(--hue-green-ink)";
  return (
    <span title={name ? `${name} (hiring manager)` : "Hiring manager"} style={{ fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 999, background: bg, color: fg, whiteSpace: "nowrap" }}>
      HM · {TAKE_LABEL[take]}
    </span>
  );
}

// Files the screening run refused to score (an un-split Bulk Print, a merged
// packet, an unreadable file). Shown so a recruiter understands why a file
// produced no candidate instead of it silently disappearing.
function SkippedNotice({ skipped }: { skipped?: { file: string; reason: string }[] }) {
  if (!skipped || skipped.length === 0) return null;
  return (
    <div style={{ textAlign: "left", background: "var(--surface-warn)", border: "1px solid var(--warn-dot)", borderRadius: 12, padding: "12px 16px", margin: "0 auto 20px", maxWidth: 520 }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: "var(--warn-ink)", marginBottom: 6 }}>
        {skipped.length === 1 ? "1 file couldn’t be screened" : `${skipped.length} files couldn’t be screened`}
      </div>
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, color: "var(--ink-soft)", display: "flex", flexDirection: "column", gap: 4 }}>
        {skipped.map((s) => (
          <li key={s.file}><strong style={{ color: "var(--ink)" }}>{s.file}</strong> — {s.reason}</li>
        ))}
      </ul>
    </div>
  );
}

// The three decisions in the three semantic hues (tokens.css): green = forward,
// amber = discuss, slate = not a fit — the same mapping as the footer pills, the
// dashboard's recorded-decisions popup and the band badges, so one colour means
// one thing everywhere a person looks.
const DECISION_STYLE: Record<string, { bg: string; fg: string; label: string }> = {
  forward: { bg: "var(--hue-green-tint)", fg: "var(--hue-green-ink)", label: "Move forward" },
  discuss: { bg: "var(--hue-amber-tint)", fg: "var(--hue-amber-ink)", label: "Discuss" },
  not: { bg: "var(--hue-slate-tint)", fg: "var(--hue-slate-ink)", label: "Not a fit" },
};

const BAND_PILLS = [
  { key: "all", label: "All bands" },
  { key: "Strong", label: "Strong" },
  { key: "Possible", label: "Possible" },
  { key: "weak", label: "Weak" },
];

// The card wears its band (app.css .rev-band-*): green / amber / slate — the same
// three the badge uses, so a 200-card list reads as a gradient of fit at a glance.
const BAND_CLASS: Record<string, string> = {
  Strong: "rev-band-strong",
  Possible: "rev-band-possible",
  "Not a fit": "rev-band-not",
};

// Said ONCE, beside the gate filter, instead of a paragraph on every gate-failed
// card (pilot feedback item 14: the cards were heavy with explanation).
const GATE_RULE = "A hard gate is pass/fail. A candidate missing one stays listed but ranks below "
  + "everyone who meets every gate, whatever their score — open the evidence and override with a reason if you disagree.";

const SORT_LABEL: Record<string, string> = {
  rank: "Match rank",
  name: "Name A–Z",
  decided: "Recently decided",
};
// Without the AI's verdict (InfoSec, 2026-10-14) the server orders the list by
// decision and then name, so "Match rank" would name an order the viewer is not shown.
const SORT_LABEL_NO_VERDICT: Record<string, string> = { ...SORT_LABEL, rank: "By decision" };

// Recompute cards + footer counts after applying a decisions map (the bulk
// bar's optimistic update and its authoritative reconcile share this).
function withDecisions(
  data: ReviewResponse,
  decisions: Record<string, { action: string; reason: string }>,
  ts: string | null
): ReviewResponse {
  const cards = (data.cards ?? []).map((card) => {
    const hit = decisions[card.candidate_id];
    if (!hit) return card;
    return hit.action === "clear"
      ? { ...card, decided: null, decided_reason: null, decided_ts: null }
      : { ...card, decided: hit.action, decided_reason: hit.reason, decided_ts: ts };
  });
  return {
    ...data,
    cards,
    decision_counts: {
      forward: cards.filter((c) => c.decided === "forward").length,
      discuss: cards.filter((c) => c.decided === "discuss").length,
      not: cards.filter((c) => c.decided === "not").length,
      awaiting: cards.filter((c) => !c.decided).length,
    },
  };
}

export default function ReviewTab() {
  const { role, blind, reload: reloadRole, drawerWidth, setDrawerWidth, openStaleGate, readOnly, closed } = useWorkspace();
  const reqId = role.req_id;
  const nav = useNavigate();
  const me = useMe();
  const isHM = isHmActive(me); // hiring managers record takes, never decisions
  const taName = role.created_by || "the recruiter";
  // Distances read in the unit this requisition's market thinks in — kilometres
  // stay stored and measured (2026-09-12).
  const distanceUnit = unitsFor(role.job_location);
  const dist = (km: number | null | undefined) => formatRadius(km, distanceUnit);

  const [data, setData] = useState<ReviewResponse | null>(null);
  // The team's decisions (2026-10-21): outside the requisition's team the server sends
  // none, so the controls that show, record or tally one go too — an absent decision
  // must never read as "awaiting".
  const decisionsShown = data?.decisions_visible !== false;
  // No decisions on a closed requisition (the server answers 409), nor by anyone
  // outside the team; comments stay open, so the drawer still gets the plain readOnly.
  const frozen = readOnly || closed || !decisionsShown;
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // The selection and the view filters are REMEMBERED per requisition for the life
  // of the browser tab (item 12): switching to Candidates and back, or reloading,
  // used to throw away twenty ticked boxes. Read once, on mount (the workspace
  // remounts this tab when the requisition changes — Outlet is keyed on it).
  const [persisted] = useState(() => loadReviewState(reqId));
  const [query, setQuery] = useState(persisted?.query ?? "");
  const [tab, setTab] = useState(persisted?.tab ?? "all");
  const [band, setBand] = useState(persisted?.band ?? "all");
  const [gate, setGate] = useState(persisted?.gate ?? "all"); // all | passed | failed — shown only when the run has gates
  // Deep-linked focus (?show=full_look): the run history's "N met every
  // must-have" link lands here showing exactly those candidates — passed every
  // hard gate AND no must-have gap, the set that got the detailed look. A
  // dismissible chip, not a permanent filter: the full list is one click away.
  const [searchParams, setSearchParams] = useSearchParams();
  const [fullLook, setFullLook] = useState(searchParams.get("show") === "full_look");
  useEffect(() => {
    if (searchParams.get("show") === "full_look") setFullLook(true);
  }, [searchParams]);
  const dismissFullLook = () => {
    setFullLook(false);
    if (searchParams.has("show")) {
      searchParams.delete("show");
      setSearchParams(searchParams, { replace: true });
    }
  };
  const [sel, setSel] = useState<Set<string>>(() => new Set(persisted?.sel ?? []));
  // "View selected": narrow the list to the ticked candidates, to find them again
  // after scrolling a 200-row list (item 12). Never sticky — it clears with the selection.
  const [selOnly, setSelOnly] = useState(false);
  // "You marked some you never decided on" — shown when leaving for the Shortlist.
  const [shortlistAsk, setShortlistAsk] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [openMath, setOpenMath] = useState(false); // score click -> land on the math
  const [sortMode, setSortMode] = useState(persisted?.sortMode ?? "rank"); // rank | name | decided
  const [sortOpen, setSortOpen] = useState(false);
  const nameOf = usePersonName(); // decided_by / HM names as PEOPLE, never UPNs
  const toast = useToast();
  // The list is its own scroll region; past ~600px a "back to top" pill appears
  // (200-candidate lists were a long way from anywhere).
  const listRef = useRef<HTMLDivElement>(null);
  const [scrolled, setScrolled] = useState(false);
  const [active, setActive] = useState<number | null>(null); // j/k focus row (index into filtered)
  // HM view (2026-07-19): Not-a-fit and not-yet-decided candidates arrive
  // minimized — collapsed groups under the working set, expandable so the HM
  // can still disagree or look ahead. The working set itself collapses too
  // (default expanded).
  const [showNotFit, setShowNotFit] = useState(false);
  const [showUndecided, setShowUndecided] = useState(false);
  const [showMain, setShowMain] = useState(true);
  // Notification deep links (?focus=cand:… / ?focus=comment:…): flash the rows
  // (expanding a minimized group when the target hides inside one), or open the
  // drawer straight onto the comment that caused the notification.
  const [flashIds, setFlashIds] = useState<Set<string>>(new Set());
  const [focusComment, setFocusComment] = useState<string | null>(null);

  const reload = () => {
    setLoading(true);
    get<ReviewResponse>(`/api/roles/${reqId}/review?blind=${blind}`)
      .then((d) => { setData(d); setLoading(false); })
      .catch((e) => { setError(e.message); setLoading(false); });
  };

  // Blind review only renames the cards (ids are unchanged), so the selection
  // survives the toggle; it used to be cleared here.
  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [reqId, blind]);

  // Remember the working state on every change (see reviewState.ts for what is
  // deliberately NOT remembered).
  useEffect(() => {
    saveReviewState(reqId, { sel: Array.from(sel), tab, band, gate, query, sortMode });
  }, [reqId, sel, tab, band, gate, query, sortMode]);
  useEffect(() => { if (sel.size === 0) setSelOnly(false); }, [sel.size]);

  const cards = data?.cards ?? [];
  // The verdict reaches HR roles only; everyone else's cards carry no band or score,
  // so the controls that filter or sort by them go too — rendered by presence.
  const verdictShown = hasVerdict(cards);
  const baseSortLabels = verdictShown ? SORT_LABEL : SORT_LABEL_NO_VERDICT;
  const sortLabels = decisionsShown ? baseSortLabels
    : Object.fromEntries(Object.entries(baseSortLabels).filter(([key]) => key !== "decided"));
  // A remembered decision filter or "recently decided" sort means nothing to a viewer
  // who is shown no decisions — fall back to the whole list in match order.
  useEffect(() => {
    if (decisionsShown) return;
    if (tab !== "all") setTab("all");
    if (sortMode === "decided") setSortMode("rank");
  }, [decisionsShown, tab, sortMode]);

  // A remembered selection may name candidates a re-screen has since dropped —
  // prune it to the cards that exist, so a bulk call never posts a stale id.
  useEffect(() => {
    if (!data?.cards) return;
    const ids = new Set(data.cards.map((card) => card.candidate_id));
    setSel((prev) => {
      const next = new Set(Array.from(prev).filter((id) => ids.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [data]);

  useEffect(() => {
    const focus = searchParams.get("focus");
    if (!focus || !data?.ready || cards.length === 0) return;
    const parts = focus.split(":");
    const byId = new Map(cards.map((card) => [card.candidate_id, card]));
    const expandFor = (ids: string[]) => {
      if (ids.some((id) => byId.get(id)?.decided === "not")) setShowNotFit(true);
      if (ids.some((id) => byId.has(id) && !byId.get(id)?.decided)) setShowUndecided(true);
    };
    if (parts[0] === "cand") {
      const ids = (parts[1] ?? "").split(",").filter((id) => byId.has(id));
      if (ids.length) {
        expandFor(ids);
        // ?open=1 opens the drawer straight away — the Shortlist's second pass is
        // about re-reading the evidence, so it links INTO the drawer (item 16).
        if (searchParams.get("open") === "1") {
          setOpenMath(false);
          setFocusComment(null);
          setOpenId(ids[0]);
        }
        setFlashIds(new Set(ids));
        window.setTimeout(() => {
          document.getElementById(`cand-${ids[0]}`)?.scrollIntoView?.({ block: "center", behavior: "smooth" });
        }, 120);
        window.setTimeout(() => setFlashIds(new Set()), 3200);
      }
    } else if (parts[0] === "comment" && parts[1] && byId.has(parts[1])) {
      expandFor([parts[1]]);
      setFocusComment(parts[2] ?? "latest");
      setOpenMath(false);
      setOpenId(parts[1]);
    }
    searchParams.delete("focus");
    searchParams.delete("open");
    setSearchParams(searchParams, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, data?.ready, cards.length]);

  // Where the reviewer came from, so they can get back without hunting for the tab.
  // Read once on mount: the param is cleaned off the URL above.
  const [cameFromShortlist] = useState(() => searchParams.get("from") === "shortlist");
  const hasGates = cards.some((card) => (card.gates?.total ?? 0) > 0);
  // The location lens (2026-08-20). Off by default and never sticky: it is a way of
  // LOOKING at the list, not a property of the shortlist, and a filter that silently
  // stayed on would quietly hide people.
  const workLoc = data?.work_location ?? null;
  const [nearOnly, setNearOnly] = useState(false);
  const [distanceOpen, setDistanceOpen] = useState(false);
  // Candidates whose distance is UNKNOWN (no location stated, or a place the gazetteer
  // doesn't know) are included by DEFAULT and the recruiter opts out explicitly. The
  // system must never read an omission as "far away" — but a reviewer narrowing their
  // own shortlist is a different act, so the choice exists and starts inclusive.
  const [includeUnknown, setIncludeUnknown] = useState(true);
  // Can a distance be worked out from the JOB's city at all? `undefined` on an older
  // payload means "assume yes" — the field is additive. When it is false the filter has
  // nothing to filter on, and saying why ONCE beats every row reading "unknown".
  const placeKnown = workLoc?.place_known !== false;
  const unknownPlaceNote = workLoc
    ? `We don't have ${workLoc.place} in the location list, so no candidate's distance could be worked out. Nobody is filtered out — ask an administrator to add it.`
    : "";
  const nearCount = useMemo(() => cards.filter((c) => c.location_within === true).length, [cards]);
  const unknownCount = useMemo(() => cards.filter((c) => c.location_within == null).length, [cards]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matches = cards.filter((card) => {
      if (fullLook && ((card.gates?.failed?.length ?? 0) > 0 || (card.must_have_gaps?.length ?? 0) > 0)) return false;
      if (band === "Strong" && card.band !== "Strong") return false;
      if (band === "Possible" && card.band !== "Possible") return false;
      if (band === "weak" && card.band !== "Not a fit") return false;
      if (gate === "passed" && card.gates?.failed.length) return false;
      if (gate === "failed" && !card.gates?.failed.length) return false;
      if (tab === "awaiting" && card.decided) return false;
      if (tab === "forward" && card.decided !== "forward") return false;
      if (tab === "discuss" && card.decided !== "discuss") return false;
      if (tab === "not" && card.decided !== "not") return false;
      // Only a KNOWN distance outside the radius hides a candidate. `null` — no stated
      // location, or a place the gazetteer could not resolve — always stays visible.
      // Absence of evidence must not read as evidence of distance; filtering on an
      // omission is how a convenience lens turns into a silent rejection.
      if (nearOnly && card.location_within === false) return false;
      if (nearOnly && !includeUnknown && card.location_within == null) return false;
      if (selOnly && !sel.has(card.candidate_id)) return false;
      if (q && !card.name.toLowerCase().includes(q)) return false;
      return true;
    });
    if (sortMode === "name") matches.sort((a, b) => a.name.localeCompare(b.name));
    // most recently decided first; undecided (no ts) sink to the bottom
    if (sortMode === "decided") matches.sort((a, b) => (b.decided_ts ?? "").localeCompare(a.decided_ts ?? ""));
    return matches;
  }, [cards, query, tab, band, gate, sortMode, fullLook, nearOnly, includeUnknown, selOnly, sel]);

  // What each pill would show with EVERY OTHER filter applied and its own ignored —
  // so a zero means "nobody here", not an artefact of the pill's own state. One
  // helper for both rows: a filter that cannot change the list says so (item 7).
  const countsIgnoring = useMemo(() => {
    const q = query.trim().toLowerCase();
    const passes = (card: ReviewCard, skip: "band" | "gate") => {
      if (fullLook && ((card.gates?.failed?.length ?? 0) > 0 || (card.must_have_gaps?.length ?? 0) > 0)) return false;
      if (skip !== "gate") {
        if (gate === "passed" && card.gates?.failed.length) return false;
        if (gate === "failed" && !card.gates?.failed.length) return false;
      }
      if (skip !== "band") {
        if (band === "Strong" && card.band !== "Strong") return false;
        if (band === "Possible" && card.band !== "Possible") return false;
        if (band === "weak" && card.band !== "Not a fit") return false;
      }
      if (tab === "awaiting" && card.decided) return false;
      if (tab === "forward" && card.decided !== "forward") return false;
      if (tab === "discuss" && card.decided !== "discuss") return false;
      if (tab === "not" && card.decided !== "not") return false;
      if (nearOnly && card.location_within === false) return false;
      if (nearOnly && !includeUnknown && card.location_within == null) return false;
      if (selOnly && !sel.has(card.candidate_id)) return false;
      if (q && !card.name.toLowerCase().includes(q)) return false;
      return true;
    };
    const forBands = cards.filter((card) => passes(card, "band"));
    const forGates = cards.filter((card) => passes(card, "gate"));
    return {
      bands: {
        all: forBands.length,
        Strong: forBands.filter((c) => c.band === "Strong").length,
        Possible: forBands.filter((c) => c.band === "Possible").length,
        weak: forBands.filter((c) => c.band === "Not a fit").length,
      } as Record<string, number>,
      gates: {
        all: forGates.length,
        passed: forGates.filter((c) => !c.gates?.failed.length).length,
        failed: forGates.filter((c) => (c.gates?.failed.length ?? 0) > 0).length,
      } as Record<string, number>,
    };
  }, [cards, query, tab, gate, band, fullLook, nearOnly, includeUnknown, selOnly, sel]);
  const bandCounts = countsIgnoring.bands;
  const gateCounts = countsIgnoring.gates;

  // A band filter that can no longer match anything (the gate filter moved under it)
  // silently hid every candidate; drop back to "all bands" instead. Only once the
  // run is actually loaded — on the first render every count is 0, and resetting
  // then would wipe the filter a returning reviewer had remembered for them.
  useEffect(() => {
    if (!data?.ready || cards.length === 0) return;
    if (band !== "all" && (bandCounts[band] ?? 0) === 0) setBand("all");
    if (gate !== "all" && (gateCounts[gate] ?? 0) === 0) setGate("all");
  }, [band, bandCounts, gate, gateCounts, data?.ready, cards.length]);

  // Is anything narrowing the list? Drives the "N of M shown · Clear" readout (item 6).
  const filtersActive = band !== "all" || gate !== "all" || tab !== "all" || !!query.trim()
    || nearOnly || fullLook || selOnly;
  const clearFilters = () => {
    setBand("all"); setGate("all"); setTab("all"); setQuery(""); setNearOnly(false); setSelOnly(false);
    dismissFullLook();
  };
  // What the distance dialog's apply button would leave on screen.
  const wouldShowNear = nearCount + (includeUnknown ? unknownCount : 0);

  // Optimistic single decision — the row flips instantly, the server's echo
  // reconciles the note + counts, and a failure rolls the row back with an
  // inline error. reloadRole() refreshes the header/tab counts in the
  // background (post-F2 it no longer unmounts the workspace). Returns whether
  // it stuck, so the drawer can revert its own highlight.
  async function decide(candidateId: string, action: string, reason?: string): Promise<boolean> {
    if (!data) return false;
    const snapshot = data;
    const now = new Date().toISOString();
    setData(withDecisions(data, { [candidateId]: { action, reason: reason?.trim() ?? "" } }, now));
    try {
      const res = await post<DecisionResponse>(`/api/roles/${reqId}/decisions`, {
        candidate_id: candidateId, action, reason,
      });
      setData((prev) =>
        prev
          ? {
              // guard: if the backend hasn't shipped the enriched response yet,
              // the optimistic value simply stands.
              ...(res.decision ? withDecisions(prev, { [candidateId]: res.decision }, now) : prev),
              ...(res.decision_counts ? { decision_counts: res.decision_counts } : {}),
            }
          : prev
      );
      // Shortlist reads /review through the cache — drop the role prefix so it
      // reflects this decision on next mount.
      invalidate(`/api/roles/${reqId}`);
      reloadRole();
      return true;
    } catch (e) {
      setData(snapshot);
      setError((e as Error).message);
      return false;
    }
  }

  // Reconfirm a decision carried over from scores that have since moved
  // (2026-07-20). Clears the card's "reconfirm" badge; the server re-stamps the
  // decision onto the current scores (note preserved).
  const clearCardBadge = (cid: string) =>
    setData((prev) => (prev ? { ...prev, cards: (prev.cards ?? []).map((c) =>
      c.candidate_id === cid ? { ...c, decided_reconfirm: false } : c) } : prev));

  async function reconfirmOne(cid: string) {
    try { await post(`/api/roles/${reqId}/candidates/${cid}/reconfirm`); clearCardBadge(cid); reloadRole(); }
    catch (e) { setError((e as Error).message); }
  }

  async function reconfirmAll() {
    try {
      await post(`/api/roles/${reqId}/decisions/reconfirm-all`);
      setData((prev) => (prev ? { ...prev, cards: (prev.cards ?? []).map((c) => ({ ...c, decided_reconfirm: false })) } : prev));
      reloadRole();
    } catch (e) { setError((e as Error).message); }
  }

  // ONE decision path for a group (2026-09-07, item 14). A ticked box used to do
  // nothing until you found the bar at the top of the page; someone selected
  // fourteen candidates, hit Shortlist, saw one, and clicked "Clear decision" to
  // start over — losing the lot. Now the selection is a SCOPE: deciding on a card
  // that is part of it decides for all of them, and the selection clears, so the
  // next decision is a single one again. The bar says this in words.
  async function decideSelected(action: string) {
    const ids = Array.from(sel);
    if (ids.length === 0) return;
    await bulkDecide(action);
    const label = { forward: "Move forward", discuss: "Discuss", not: "Not a fit" }[action] ?? action;
    toast(`${label} recorded for ${ids.length} candidate${ids.length === 1 ? "" : "s"}`);
  }

  // The bulk bar's ONE call: optimistic rows, reconciled from the response,
  // rolled back if the server refuses the batch (all-or-nothing).
  async function bulkDecide(action: string) {
    const ids = Array.from(sel);
    if (!data || ids.length === 0) return;
    const snapshot = data;
    const now = new Date().toISOString();
    setData(withDecisions(data, Object.fromEntries(ids.map((id) => [id, { action, reason: "" }])), now));
    setSel(new Set());
    try {
      const res = await post<BatchDecisionResponse>(`/api/roles/${reqId}/decisions/batch`, {
        candidate_ids: ids,
        action,
      });
      setData((prev) => (prev ? withDecisions(prev, res.decisions, now) : prev));
      invalidate(`/api/roles/${reqId}`);
      reloadRole();
    } catch (e) {
      setData(snapshot);
      setSel(new Set(ids));
      setError((e as Error).message);
    }
  }

  // --- hiring-manager takes (input, never decisions) ---
  const [takeMsg, setTakeMsg] = useState<string | null>(null);

  function optimisticTake(ids: string[], value: Take) {
    setData((prev) =>
      prev
        ? {
            ...prev,
            cards: (prev.cards ?? []).map((c) =>
              ids.includes(c.candidate_id) ? { ...c, hm_take: { take: value, hm_name: me?.user ?? "" } } : c
            ),
          }
        : prev
    );
  }

  // A re-take supersedes on the server, so a take is always set (no clear).
  async function take(candidateId: string, value: Take, note?: string) {
    optimisticTake([candidateId], value);
    try {
      await post(`/api/roles/${reqId}/takes`, {
        takes: [{ candidate_id: candidateId, take: value, ...(note ? { note } : {}) }],
      });
    } catch (e) {
      setError((e as Error).message);
      reload();
    }
  }

  async function bulkTake(value: Take) {
    const ids = Array.from(sel);
    if (ids.length === 0) return;
    optimisticTake(ids, value);
    setSel(new Set());
    try {
      await post(`/api/roles/${reqId}/takes`, { takes: ids.map((id) => ({ candidate_id: id, take: value })) });
    } catch (e) {
      setError((e as Error).message);
      reload();
    }
  }

  async function sendTakes() {
    try {
      await post(`/api/roles/${reqId}/takes/send`, {});
      setTakeMsg(`Takes sent to ${taName}.`);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // What a decision click on ONE card means: itself, or — when that card is part of
  // the marked set — the whole set. Acting on something you marked acts on the mark.
  const decideFor = async (candidateId: string, action: string, reason?: string): Promise<boolean> => {
    if (sel.size > 0 && sel.has(candidateId)) { await decideSelected(action); return true; }
    return decide(candidateId, action, reason);
  };
  const takeFor = (candidateId: string, value: Take) => {
    if (sel.size > 0 && sel.has(candidateId)) return void bulkTake(value);
    return take(candidateId, value);
  };

  function toggleSel(id: string) {
    setSel((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // Select-all works over the FILTERED list, not the whole run.
  const allFilteredSelected = filtered.length > 0 && filtered.every((c) => sel.has(c.candidate_id));
  function toggleSelectAll() {
    setSel(allFilteredSelected ? new Set() : new Set(filtered.map((c) => c.candidate_id)));
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      switch (e.key) {
        case "j":
          setActive((a) => (filtered.length ? Math.min((a ?? -1) + 1, filtered.length - 1) : null));
          break;
        case "k":
          setActive((a) => (filtered.length ? Math.max((a ?? filtered.length) - 1, 0) : null));
          break;
        case "Enter":
        case "o":
          if (active != null && filtered[active]) setOpenId(filtered[active].candidate_id);
          break;
        case "1":
        case "2":
        case "3": {
          if (isHM || frozen) break; // HMs don't decide via the keyboard; takes are click-only
          const action = { "1": "forward", "2": "discuss", "3": "not" }[e.key]!;
          const target = openId ?? (active != null ? filtered[active]?.candidate_id : undefined);
          if (target) {
            if (data?.staleness) openStaleGate();
            else void decide(target, action);
          }
          break;
        }
        case "Escape":
          // innermost thing first: an open dialog/menu, then the drawer, then the
          // selection (2026-09-06: Escape on the distance dialog used to wipe the
          // selection underneath it instead of closing the dialog)
          if (distanceOpen) setDistanceOpen(false);
          else if (sortOpen) setSortOpen(false);
          else if (openId) setOpenId(null);
          else setSel(new Set());
          break;
        default:
          return;
      }
      e.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtered, active, openId, isHM, frozen, distanceOpen, sortOpen]);

  if (loading && !data) {
    return <div style={{ height: "100%", overflowY: "auto" }}><CenterLoading label="Loading shortlist…" /></div>;
  }

  const dc = data?.decision_counts;
  // P51: scores produced under older published rules. ANY stale mode pauses new
  // decisions (the server 409s them too); "locked" (decisions on record) gets
  // the full banner, "soft"/"auto" a quiet line.
  const stale = data?.staleness ?? null;
  const decidedCount = (dc?.forward ?? 0) + (dc?.discuss ?? 0) + (dc?.not ?? 0);
  // Marked, but no decision recorded — the work someone would lose by walking away.
  const undecidedMarked = cards.filter((c) => sel.has(c.candidate_id) && !c.decided).length;

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", position: "relative" }}>
      {/* filter header — ONE compact row (wraps when narrow). Order follows what
          recruiters reach for first (pilot feedback item 14): the GATES, then the
          location lens, then bands; search and sort sit at the right. The candidate
          list below is the working area and gets every remaining pixel. */}
      {data?.ready && (
        <div style={{ flex: "0 0 auto", display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", padding: "9px 24px", background: "var(--white)", borderBottom: "1px solid var(--border-subtle)" }}>
          {/* THE number a recruiter watches while narrowing the list (item 6): how many
              are left for their judgment, out of the whole batch. */}
          {/* The width is RESERVED from the total's digit count (2026-09-07): as a
              search narrowed 237 -> 23 -> 2 the readout shrank, the row reflowed and
              the Sort control hopped to the next line and back on every keystroke. */}
          <div className="shown-count" role="status" aria-live="polite"
               style={{ ["--shown-ch" as string]: `${String(cards.length).length}ch` }}
               aria-label={filtersActive ? `${filtered.length} of ${cards.length} candidates shown` : `${cards.length} candidates`}>
            <span className="shown-n">{filtered.length}</span>
            <span className="shown-of">
              {filtersActive ? <>of <b>{cards.length}</b> shown</> : (cards.length === 1 ? "candidate" : "candidates")}
            </span>
            {/* Always rendered, hidden when there is nothing to clear: appearing and
                disappearing changed the readout's width and shoved the row (item 7). */}
            <button className="shown-clear" onClick={clearFilters}
                    aria-hidden={!filtersActive} tabIndex={filtersActive ? 0 : -1}
                    style={{ visibility: filtersActive ? "visible" : "hidden" }}
                    title="Show every candidate again">Clear</button>
          </div>
          {hasGates && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
              {([["all", "Gates · all"], ["passed", "Passed"], ["failed", "Gate not met"]] as const).map(([key, label]) => {
                const n = gateCounts[key] ?? 0;
                // Same rule as the band pills: a filter that can only produce an
                // empty list is disabled in place, never removed (removing reflows).
                const off = key !== "all" && n === 0;
                return (
                  <button
                    key={key}
                    onClick={() => !off && setGate(key)}
                    disabled={off}
                    aria-disabled={off || undefined}
                    title={off ? "Nobody in the current view is in this group." : undefined}
                    className={"pill" + (gate === key ? " active" : "") + (off ? " is-off" : "")}
                  >
                    {label}{key === "all" ? "" : ` · ${n}`}
                  </button>
                );
              })}
              <InfoDot label="How hard gates affect ranking" content={GATE_RULE} />
            </div>
          )}
          {/* The location lens — only offered when the requisition names a place.
              The distance itself is set HERE (2026-08-26): "how far am I willing to
              look?" is a reviewer's question, and answering it two tabs away from
              the list it filters made no sense. ONE behaviour now (2026-09-06, item
              7): the pill opens the distance dialog, where the distance is set and the
              filter applied in the same place; while the filter is on, the pill's ✕
              shows everyone again. (The old pill toggled and a tiny gear beside it
              edited — two controls nobody could tell apart.) */}
          {workLoc?.place && (
            <span className={"pill-split" + (nearOnly ? " active" : "")}>
              <button
                className="pill-main"
                onClick={() => setDistanceOpen(true)}
                aria-pressed={nearOnly}
                aria-haspopup="dialog"
                title={!placeKnown
                  ? unknownPlaceNote
                  : workLoc.radius_km
                  ? (nearOnly
                      ? (includeUnknown && unknownCount > 0
                          ? `Showing ${nearCount} within ${dist(workLoc.radius_km)} of ${workLoc.place} and ${unknownCount} whose distance is unknown — click to change`
                          : `Showing candidates within ${dist(workLoc.radius_km)} of ${workLoc.place} — click to change the distance`)
                      : `Click to filter by distance from ${workLoc.place}`)
                  : `Set how far from ${workLoc.place} to look`}
              >
                <IconPin size={13} />
                {/* When the JOB's own city is the unresolvable side, every candidate
                    reads "distance unknown" and the filter can only ever hide people
                    for a gap in our table. Say that here rather than offering a
                    distance nobody can set (2026-09-11, Budapest). */}
                {!placeKnown
                  ? `No distances for ${workLoc.place}`
                  : workLoc.radius_km
                  ? `Within ${dist(workLoc.radius_km)} of ${workLoc.place}`
                  : `Set distance from ${workLoc.place}`}
                {placeKnown && workLoc.radius_km ? ` · ${nearOnly ? wouldShowNear : nearCount}` : ""}
              </button>
              {nearOnly && (
                <button className="pill-x" onClick={() => setNearOnly(false)} aria-label="Show everyone again" title="Show everyone again">
                  <IconX size={12} />
                </button>
              )}
            </span>
          )}
          {/* Band pills carry their count UNDER the current gate filter (2026-09-07).
              A gate failure forces the band to "Not a fit" in scoring.py, so with
              "Gate not met" selected, Strong and Possible are logically empty — they
              are disabled and say why, rather than disappearing (a pill that vanishes
              reflows the row, which is the dancing this pass is removing). */}
          {verdictShown && <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {BAND_PILLS.map((b) => {
              const n = bandCounts[b.key] ?? 0;
              const off = b.key !== "all" && n === 0;
              return (
                <button
                  key={b.key}
                  onClick={() => !off && setBand(b.key)}
                  disabled={off}
                  aria-disabled={off || undefined}
                  title={off
                    ? (gate === "failed" && (b.key === "Strong" || b.key === "Possible")
                        ? "A candidate who misses a hard requirement is never Strong or Possible — that's what a gate means."
                        : "Nobody in the current view is in this band.")
                    : undefined}
                  className={"pill" + (band === b.key ? " active" : "") + (off ? " is-off" : "")}
                >
                  {b.label}{b.key !== "all" ? ` · ${n}` : ""}
                </button>
              );
            })}
          </div>}
          {fullLook && (
            <button onClick={dismissFullLook} className="pill active" title="These candidates passed every hard gate and have no must-have gap. Click to show everyone again.">
              Showing: met every must-have ✕
            </button>
          )}
          {!isHM && data?.batch_quality && <BatchQualityChip bq={data.batch_quality} />}
          <span style={{ flex: "1 1 auto" }} />
          <div style={{ position: "relative", flex: "0 1 200px", minWidth: 150 }}>
            <span style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--ink-muted)", display: "inline-flex" }}><IconSearch size={15} /></span>
            <input className="input" style={{ height: 34, paddingLeft: 34 }} placeholder="Search by name…" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          <div style={{ position: "relative" }}>
            <button
              onClick={() => setSortOpen((o) => !o)}
              aria-expanded={sortOpen}
              style={{ display: "inline-flex", alignItems: "center", gap: 6, height: 34, padding: "0 14px", border: "1px solid var(--border-subtle)", borderRadius: 10, background: "var(--white)", color: "var(--ink-soft)", fontFamily: "inherit", fontSize: 12.5, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap" }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h13" /><path d="M3 12h9" /><path d="M3 18h5" /><path d="m17 8 3 3 3-3" /><path d="M20 6v5" /></svg>
              Sort: {sortLabels[sortMode]}
            </button>
            {sortOpen && (
              <div style={{ position: "absolute", top: "calc(100% + 4px)", right: 0, zIndex: 30, background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 10, boxShadow: "var(--shadow-md)", minWidth: 180, overflow: "hidden" }}>
                {Object.entries(sortLabels).map(([key, label]) => (
                  <button
                    key={key}
                    onClick={() => { setSortMode(key); setSortOpen(false); }}
                    style={{ display: "block", width: "100%", textAlign: "left", padding: "9px 14px", border: 0, background: sortMode === key ? "var(--brand-green-tint)" : "var(--white)", color: sortMode === key ? "var(--brand-green-deep)" : "var(--ink)", fontFamily: "inherit", fontSize: 12.5, fontWeight: 700, cursor: "pointer" }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {distanceOpen && workLoc && (
        <OverlayRoot onDismiss={() => setDistanceOpen(false)} zIndex={60} align="top">
          <div role="dialog" aria-label="Distance from the job location" className="rise"
               style={{ position: "relative", width: "min(560px, 92%)", background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, boxShadow: "var(--shadow-lg)", padding: "18px 22px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 10 }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                {/* The hero is what the button will DO, not one part of it. It used to
                    show `nearCount` while the CTA below said `wouldShowNear`, so with the
                    unknown-distance box ticked the dialog said 16 and the button said
                    "Show only these 18" (2026-09-10). The sentence follows the number:
                    a total that includes unknowns cannot be described as "within N km". */}
                <span style={{ fontSize: 26, fontWeight: 800, color: "var(--brand-green-deep)", fontVariantNumeric: "tabular-nums" }}>
                  {placeKnown && workLoc.radius_km ? wouldShowNear : "—"}
                </span>
                {/* Facts, not an explanation (2026-09-11, pilot ask 4): a dialog you
                    opened to DO something should not be read. What the lens is and
                    what it deliberately does not do moved to the info dot. */}
                <span style={{ fontSize: 13.5, color: "var(--ink)" }}>
                  {!placeKnown
                    ? `No distances available for ${workLoc.place}`
                    : !workLoc.radius_km
                    ? `Set a distance from ${workLoc.place}`
                    : includeUnknown && unknownCount > 0
                      ? `of ${cards.length} — ${nearCount} within ${workLoc.radius_km} km of ${workLoc.place}, ${unknownCount} unknown`
                      : `of ${cards.length} within ${workLoc.radius_km} km of ${workLoc.place}`}
                </span>
                <InfoDot
                  label="About the distance filter"
                  content="Straight-line distance from the place each résumé states, worked out when the candidate was screened. Never scored, never rejects anyone — and only a KNOWN distance beyond the limit hides someone."
                />
              </div>
              <button onClick={() => setDistanceOpen(false)} aria-label="Close" style={{ flex: "0 0 auto", width: 30, height: 30, borderRadius: "50%", border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink-soft)", cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
                <IconX size={14} />
              </button>
            </div>
            {/* The one place the real reason is said. Everything below it — the
                distance control, the unknown-distance box, the apply button — would
                only offer a filter that cannot work, and applying it would empty the
                list for a gap in our table rather than anything about the people. */}
            {!placeKnown && (
              <div style={{ fontSize: 13, lineHeight: 1.5, color: "var(--hue-blue-ink)", background: "var(--hue-blue-tint)", border: "1px solid var(--border-subtle)", borderRadius: 10, padding: "10px 12px" }}>
                {unknownPlaceNote}
              </div>
            )}
            {placeKnown && (!isHM ? (
              <LocationRadius
                pack={role.job_location}
                value={workLoc}
                mode="radius"
                onChange={async (next) => {
                  try {
                    await put(`/api/roles/${reqId}/details`, { work_location: next });
                    invalidate(`/api/roles/${reqId}`);
                    reload();
                  } catch {
                    /* the pill keeps its current distance; nothing is lost */
                  }
                }}
              />
            ) : (
              <div style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>The recruiter sets the distance; you can filter by it.</div>
            ))}
            {/* Unknown distance = no location on the résumé, or a place the gazetteer could
                not find. Shown by default: an omission is not evidence of distance. The
                box is phrased as what it DOES ("also show…"), not as a system judgment. */}
            {placeKnown && (
            <label style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: 13, color: "var(--ink)", margin: "12px 0 0", cursor: "pointer", lineHeight: 1.45 }}>
              <input type="checkbox" checked={includeUnknown}
                     onChange={(e) => setIncludeUnknown(e.target.checked)}
                     style={{ accentColor: "var(--brand-green)", marginTop: 3 }} />
              <span title="No location on the résumé, or a place we couldn't find">
                Also show the {unknownCount} whose distance is unknown
              </span>
            </label>
            )}
            {placeKnown && (
            <div style={{ marginTop: 14, display: "flex", justifyContent: "flex-end", gap: 8, flexWrap: "wrap" }}>
              {nearOnly && (
                <Button size="sm" variant="ghost" onClick={() => { setNearOnly(false); setDistanceOpen(false); }}>
                  Show everyone
                </Button>
              )}
              {/* Applying is the dialog's one primary action: setting a distance IS the
                  request to see who is near. Disabled until there is a distance. */}
              <span title={workLoc.radius_km ? undefined : "Set a distance first"} style={{ display: "inline-flex" }}>
                <Button size="sm"
                        disabled={!workLoc.radius_km}
                        onClick={() => { setDistanceOpen(false); if (workLoc?.radius_km) setNearOnly(true); }}>
                  {workLoc.radius_km ? `Show only these ${wouldShowNear}` : "Set a distance first"}
                </Button>
              </span>
            </div>
            )}
          </div>
        </OverlayRoot>
      )}

      {error && <div style={{ padding: 32 }}><PageError message={error} /></div>}

      {/* HM view, nothing shared yet: the recruiter decides WHEN the shortlist
          is ready — until then this tab is deliberately empty (2026-07-19). */}
      {data?.ready && isHM && data.hm_shared === false && (
        <div style={{ flex: "1 1 auto", display: "flex", alignItems: "center", justifyContent: "center", padding: 40 }}>
          <div style={{ maxWidth: 520, textAlign: "center" }}>
            <span style={{ width: 56, height: 56, borderRadius: "50%", background: "var(--accent-blue-tint)", color: "var(--info-ink)", display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 16 }}>
              <IconSearch size={26} />
            </span>
            <h2 style={{ fontSize: 19, margin: "0 0 8px" }}>Nothing shared with you yet</h2>
            <p style={{ fontSize: 14, color: "var(--ink-soft)", margin: 0 }}>
              The recruiter shares a shortlist once their review is done — you'll get a
              notification, and the candidates appear here for your takes.
            </p>
          </div>
        </div>
      )}

      {/* gate (not screened) */}
      {data && !data.ready && (
        <div style={{ flex: "1 1 auto", display: "flex", alignItems: "center", justifyContent: "center", padding: 40 }}>
          <div style={{ maxWidth: 520, textAlign: "center" }}>
            <span style={{ width: 56, height: 56, borderRadius: "50%", background: "var(--accent-blue-tint)", color: "var(--hue-blue-ink)", display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 16 }}>
              <IconSearch size={26} />
            </span>
            <h2 style={{ fontSize: 19, margin: "0 0 8px" }}>No candidates to review yet</h2>
            <p style={{ fontSize: 14, color: "var(--ink-soft)", margin: "0 0 20px" }}>
              Add résumés on the Candidates tab and screen them against the approved checklist — the ranked shortlist and reasoning appear here.
            </p>
            <SkippedNotice skipped={data.skipped} />
            <Button onClick={() => nav(tabPath(reqId, "candidates"))}><IconSparkle size={16} /> Go to screening</Button>
          </div>
        </div>
      )}

      {/* files the run refused to score — shown above the shortlist too */}
      {data?.ready && data.skipped && data.skipped.length > 0 && (
        <div style={{ flex: "0 0 auto", padding: "12px 32px 0" }}>
          <SkippedNotice skipped={data.skipped} />
        </div>
      )}

      {/* decisions carried over from scores that have since changed — kept, not
          wiped (2026-07-20); one click reconfirms them all on the new scores */}
      {!isHM && !frozen && data?.ready && (() => {
        const n = (data.cards ?? []).filter((c) => c.decided_reconfirm).length;
        if (!n) return null;
        return (
          <div style={{ flex: "0 0 auto", padding: "12px 32px 0" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12, background: "var(--surface-warn)", border: "1px solid var(--warn-border)", borderRadius: 12, padding: "10px 16px", flexWrap: "wrap", maxWidth: 1120 }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: "var(--warn-ink)" }}>
                {n} decision{n === 1 ? "" : "s"} carried over from earlier scores — reconfirm to bring {n === 1 ? "it" : "them"} onto the current scores.
              </span>
              <button
                onClick={() => void reconfirmAll()}
                style={{ marginLeft: "auto", height: 30, padding: "0 14px", borderRadius: 8, border: "1px solid var(--warn-border)", background: "var(--white)", color: "var(--warn-ink)", fontFamily: "inherit", fontSize: 12, fontWeight: 700, cursor: "pointer" }}
              >
                Reconfirm all
              </button>
            </div>
          </div>
        );
      })()}

      {/* the design's stale banner: the rules changed after this screen ran */}
      {data?.ready && stale && stale.mode === "locked" && (
        <div style={{ flex: "0 0 auto", padding: "12px 32px 0" }}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 12, background: "var(--surface-warn)", border: "1px solid var(--warn-border, var(--accent-yellow))", borderRadius: 12, padding: "12px 16px", flexWrap: "wrap", maxWidth: 1120 }}>
            <span style={{ fontSize: 16, flex: "0 0 auto" }}>⏱</span>
            <div style={{ flex: "1 1 320px", minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--warn-ink)" }}>
                The rules changed after this screen ran
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", margin: "7px 0 4px" }}>
                {stale.details.map((d) => [
                  <span key={d.headline} style={{ fontSize: 11, fontWeight: 700, padding: "3px 9px", borderRadius: 999, background: "var(--white)", border: "1px solid var(--warn-border, var(--accent-yellow))", color: "var(--warn-ink)" }}>{d.headline}</span>,
                  ...d.changes.slice(0, 4).map((ch) => (
                    <span key={`${d.headline}-${ch.label}`} style={{ fontSize: 11, fontWeight: 700, padding: "3px 9px", borderRadius: 999, background: "var(--white)", border: "1px solid var(--warn-border, var(--accent-yellow))", color: "var(--warn-ink)" }}>
                      {ch.label}: {ch.from} → {ch.to}
                    </span>
                  )),
                ])}
              </div>
              <div style={{ fontSize: 12, color: "var(--ink-soft)", lineHeight: 1.5 }}>
                Decisions already recorded stay valid against the old rules and stay in the audit trail.
                Recording decisions is paused until the re-screen, so old numbers never mix with new judgments.
              </div>
            </div>
            <Button size="sm" onClick={openStaleGate}>Re-screen with current rules</Button>
          </div>
        </div>
      )}
      {data?.ready && stale && stale.mode !== "locked" && (
        <div style={{ flex: "0 0 auto", padding: "10px 32px 0", display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--ink-soft)" }}>
          <IconOutdated size={14} style={{ color: "var(--warn-ink)" }} />
          Hiring rules were updated — the next screen picks them up.
          <button onClick={openStaleGate} style={{ background: "none", border: 0, padding: 0, cursor: "pointer", font: "inherit", fontSize: 12.5, color: "var(--brand-green-deep)", fontWeight: 700, textDecoration: "underline" }}>
            Re-screen now
          </button>
        </div>
      )}

      {/* bulk-action bar (2026-09-06, item 12): a selection RECORDS NOTHING by itself
          — people ticked twenty boxes and wondered why the shortlist stayed empty. The
          bar says so, offers "View selected" to find the ticked rows again in a long
          list, and leads with Move forward, which is what builds the shortlist. */}
      {/* ONE row for everything about marking (2026-09-07, item 6): "Mark all" used
          to sit on its own line above the list while the marked-count bar sat above
          that — two places for one idea. Green only once something is marked. */}
      {data?.ready && !frozen && filtered.length > 0 && (
        <div className={"bulk-bar" + (sel.size > 0 ? " is-on" : "")} role="region" aria-label="Mark candidates">
          <Checkbox
            checked={allFilteredSelected}
            label={allFilteredSelected ? "Clear all marks" : "Mark all"}
            onClick={toggleSelectAll}
          />
          {sel.size > 0 ? (
            <>
              <span className="bulk-n">{sel.size} marked</span>
              <span className="bulk-hint">
                {isHM
                  ? <>Your next take applies to all {sel.size}.</>
                  : <>Your next decision applies to all {sel.size} — <strong>marking records nothing on its own</strong>.</>}
              </span>
              <span className="v-divider" />
              <button onClick={() => setSelOnly((v) => !v)} className={"pill" + (selOnly ? " active" : "")} aria-pressed={selOnly} style={{ height: 30 }}>
                {selOnly ? "Show everyone" : "View marked"}
              </button>
              <button onClick={() => setSel(new Set())} className="link-quiet" style={{ marginLeft: "auto", textDecoration: "none" }}>Clear marks</button>
            </>
          ) : (
            <span className="bulk-hint">
              Mark all {filtered.length} — to decide on them together
            </span>
          )}
        </div>
      )}

      {/* ready: list — the page's working area gets all remaining height (the
          old sticky decisions footer is gone; its counts live on the stage
          pills above, its button moved beside them). */}
      {data?.ready && !(isHM && data.hm_shared === false) && (
        <>
          {/* The list gets every remaining pixel: no max width (the scrollbar used to
              sit far from the cards), tighter padding, its own scroll region. */}
          <div ref={listRef} className="rev-list"
               onScroll={(e) => setScrolled(e.currentTarget.scrollTop > 600)}
               style={{ flex: "1 1 auto", minHeight: 0, overflowY: "auto", padding: "12px 24px 18px" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {filtered.length === 0 && (
                <div style={{ textAlign: "center", padding: "48px 20px", color: "var(--ink-soft)", fontSize: 14 }}>No candidates match these filters.</div>
              )}

              {(() => {
                const rowFor = (card: ReviewCard, i: number | null = null) => (
                  <CandidateRow
                    key={card.candidate_id}
                    card={card}
                    isHM={isHM}
                    stale={!!stale}
                    selected={sel.has(card.candidate_id)}
                    active={i != null && i === active}
                    flash={flashIds.has(card.candidate_id)}
                    onToggleSel={() => toggleSel(card.candidate_id)}
                    onOpen={() => { setOpenMath(false); setFocusComment(null); setOpenId(card.candidate_id); }}
                    onOpenMath={() => { setOpenMath(true); setFocusComment(null); setOpenId(card.candidate_id); }}
                    onOpenComments={() => { setOpenMath(false); setFocusComment("latest"); setOpenId(card.candidate_id); }}
                    onReconfirm={() => void reconfirmOne(card.candidate_id)}
                    onDecide={decideFor}
                    onTake={takeFor}
                    onStaleGate={openStaleGate}
                    readOnly={frozen}
                    nameOf={nameOf}
                  />
                );
                if (!isHM) return filtered.map((card, i) => rowFor(card, i));
                // HM view (2026-07-19): the shared working set (Move forward +
                // Discuss, collapsible, open by default) with the recruiter's
                // Not-a-fits and the not-yet-decided as MINIMIZED groups.
                const main = filtered.filter((c) => c.decided === "forward" || c.decided === "discuss");
                const notFit = filtered.filter((c) => c.decided === "not");
                const undecided = filtered.filter((c) => !c.decided);
                return (
                  <>
                    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                      <button
                        onClick={() => setShowMain((v) => !v)}
                        style={{ textAlign: "left", display: "flex", alignItems: "center", gap: 10, padding: "2px 6px", background: "none", border: 0, cursor: "pointer", fontFamily: "inherit" }}
                      >
                        <span style={{ fontSize: 13, fontWeight: 700 }}>
                          Shared with you — {main.length} candidate{main.length === 1 ? "" : "s"} to review
                        </span>
                        <span style={{ marginLeft: "auto", fontSize: 12, fontWeight: 700, color: "var(--brand-green-deep)" }}>
                          {showMain ? "Hide" : "Show"}
                        </span>
                      </button>
                      {showMain && main.map((card, i) => rowFor(card, i))}
                    </div>
                    {notFit.length > 0 && (
                      <CollapsedGroup
                        label={`Not a fit — ${notFit.length} set aside by the recruiter`}
                        open={showNotFit}
                        onToggle={() => setShowNotFit((v) => !v)}
                      >
                        {notFit.map((card) => rowFor(card))}
                      </CollapsedGroup>
                    )}
                    {undecided.length > 0 && (
                      <CollapsedGroup
                        label={`Not yet decided — ${undecided.length} still with the recruiter`}
                        open={showUndecided}
                        onToggle={() => setShowUndecided((v) => !v)}
                      >
                        {undecided.map((card) => rowFor(card))}
                      </CollapsedGroup>
                    )}
                  </>
                );
              })()}
            </div>
          </div>
          {scrolled && (
            <button className="to-top" onClick={() => listRef.current?.scrollTo({ top: 0, behavior: "smooth" })}>
              ↑ Top
            </button>
          )}

          {/* the design's sticky decisions footer */}
          <div style={{ flex: "0 0 auto", position: "sticky", bottom: 0, background: "var(--white)", borderTop: "1px solid var(--border-subtle)", padding: "10px 32px", display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", boxShadow: "0 -4px 16px rgba(34,34,34,.05)" }}>
            {!decisionsShown ? (
              <span style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>{data?.decisions_note}</span>
            ) : (<>
            <span style={{ fontSize: 12.5, fontWeight: 700 }}>Your decisions</span>
            {[
              { key: "forward", label: "Move forward", count: dc?.forward ?? 0, bg: "var(--hue-green-tint)", fg: "var(--hue-green-ink)" },
              { key: "discuss", label: "Discuss", count: dc?.discuss ?? 0, bg: "var(--hue-amber-tint)", fg: "var(--hue-amber-ink)" },
              { key: "not", label: "Not a fit", count: dc?.not ?? 0, bg: "var(--hue-slate-tint)", fg: "var(--hue-slate-ink)" },
              { key: "awaiting", label: "Awaiting", count: dc?.awaiting ?? 0, bg: "var(--white)", fg: "var(--ink-soft)" },
            ].map((f) => (
              <button
                key={f.key}
                onClick={() => setTab(tab === f.key ? "all" : f.key)}
                style={{ height: 28, padding: "0 12px", borderRadius: 999, fontSize: 11.5, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap", fontFamily: "inherit", border: tab === f.key ? "1.5px solid var(--ink)" : f.key === "awaiting" ? "1px solid var(--border-subtle)" : "1px solid transparent", background: f.bg, color: f.fg }}
              >
                {f.label} {f.count}
              </button>
            ))}
            <span style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 12 }}>
              {isHM ? (
                <>
                  {takeMsg && <span style={{ fontSize: 12.5, fontWeight: 700, color: "var(--brand-green-deep)" }}>{takeMsg}</span>}
                  <Button size="sm" title={`Takes are input — ${taName} decides.`} disabled={cards.every((card) => !card.hm_take)} onClick={sendTakes}>
                    Send takes to {taName}
                  </Button>
                </>
              ) : (
                <>
                  {/* The CTA carries the number it will carry forward (item 14), so
                      "Shortlist 1" after marking fourteen is visible BEFORE the click
                      rather than a surprise on the next page. */}
                  <span title={stale ? "The rules changed since this screen ran — re-screen first."
                    : decidedCount === 0 ? "Nothing decided yet — a mark records nothing; choose Move forward, Discuss or Not a fit." : undefined}
                        style={{ display: "inline-flex" }}>
                    <Button
                      size="sm"
                      disabled={decidedCount === 0 && !stale}
                      onClick={() => {
                        if (stale) return openStaleGate();
                        // Marked but never decided = unfinished work. Say so before
                        // leaving, and offer the view that finds them again.
                        if (undecidedMarked > 0) return setShortlistAsk(true);
                        nav(tabPath(reqId, "shortlist"));
                      }}
                    >
                      Shortlist{decidedCount > 0 ? ` ${decidedCount}` : ""} →
                    </Button>
                  </span>
                </>
              )}
            </span>
            </>)}
          </div>
        </>
      )}

      {shortlistAsk && (
        <ConfirmModal
          spec={{
            title: `${undecidedMarked} marked candidate${undecidedMarked === 1 ? "" : "s"} without a decision`,
            reason: "Marking gathers candidates so you can decide on them together — it "
              + "doesn't record anything by itself. The shortlist only carries decisions.",
            question: decidedCount > 0
              ? `The shortlist will show the ${decidedCount} you have decided on. Go back and decide on the rest, or carry on.`
              : "Nothing has been decided yet, so the shortlist will be empty.",
            cta: "Go to the shortlist anyway",
            onProceed: () => { setShortlistAsk(false); nav(tabPath(reqId, "shortlist")); },
          }}
          onCancel={() => { setShortlistAsk(false); setSelOnly(true); }}
        />
      )}

      {openId && (
        <CandidateDrawer
          pack={role.job_location}
          reqId={reqId}
          candidateId={openId}
          blind={blind}
          takeMode={isHM}
          readOnly={readOnly}
          frozen={closed}
          taName={taName}
          stale={!!stale}
          focusMath={openMath}
          focusComment={focusComment}
          width={drawerWidth}
          onWidthChange={setDrawerWidth}
          onBackdropWheel={(dy) => listRef.current?.scrollBy({ top: dy })}
          // Arrived from the Shortlist for a quick look: the way back belongs to the
          // DRAWER, since the drawer is the thing they opened (2026-09-07, item 8).
          backTo={cameFromShortlist ? { label: "Back to shortlist", go: () => nav(tabPath(reqId, "shortlist")) } : undefined}
          onClose={() => { setOpenId(null); setOpenMath(false); setFocusComment(null); }}
          onDecide={decideFor}
          onTake={takeFor}
          onStaleGate={openStaleGate}
          onCommented={(cid) => setData((prev) => prev ? {
            ...prev,
            cards: (prev.cards ?? []).map((card) =>
              card.candidate_id === cid ? { ...card, comments: (card.comments ?? 0) + 1 } : card),
          } : prev)}
          onReconfirmed={clearCardBadge}
        />
      )}
    </div>
  );
}

/** A minimized card group (HM view): Not-a-fit / not-yet-decided stay out of
 *  the way until asked for — and auto-expand when a deep link targets them. */
function CollapsedGroup({ label, open, onToggle, children }: {
  label: string; open: boolean; onToggle: () => void; children: React.ReactNode;
}) {
  return (
    <div style={{ border: "1px solid var(--neutral-150)", borderRadius: 12, background: "var(--neutral-100)" }}>
      <button
        onClick={onToggle}
        style={{ width: "100%", textAlign: "left", display: "flex", alignItems: "center", gap: 10, padding: "12px 18px", background: "none", border: 0, cursor: "pointer", fontFamily: "inherit" }}
      >
        <span style={{ fontSize: 13, fontWeight: 700, color: "var(--ink-soft)" }}>{label}</span>
        <span style={{ marginLeft: "auto", fontSize: 12, fontWeight: 700, color: "var(--brand-green-deep)" }}>
          {open ? "Hide" : "Show"}
        </span>
      </button>
      {open && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: "0 12px 12px" }}>
          {children}
        </div>
      )}
    </div>
  );
}

// Excel-style conditional formatting for the "Gates m/n" chip: the colour glides
// from red (none met) through amber to green (all met) with the pass ratio, so a
// partial 1/2 reads as "part-way", not a blanket failure. A gate failure still
// makes the candidate ineligible — the colour just shows how close they came.
export function gateChipColor(passed: number, total: number): { bg: string; fg: string } {
  const r = total > 0 ? Math.max(0, Math.min(1, passed / total)) : 1;
  const hue = Math.round(6 + r * (142 - 6)); // 6 = red → 142 = green
  return { bg: `hsl(${hue} 72% 93%)`, fg: `hsl(${hue} 60% 30%)` };
}

function Checkbox({ checked, onClick, label }: { checked: boolean; onClick: () => void; label?: string }) {
  return (
    <button
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      aria-label={label ?? (checked ? "Deselect" : "Select")}
      aria-pressed={checked}
      style={{
        flex: "0 0 auto",
        width: 22,
        height: 22,
        borderRadius: 6,
        border: checked ? "1.5px solid var(--brand-green)" : "1.5px solid var(--border-strong)",
        background: checked ? "var(--brand-green)" : "var(--white)",
        cursor: "pointer",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 0,
      }}
    >
      {checked && (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
      )}
    </button>
  );
}

/** The design score hover line: "70 base +4 signals +2 preference = 76 -
 * click to open the breakdown" (stale variant explains the pause). */
export function scoreTipLine(math: ScoreMathBrief | null | undefined, stale: boolean): string {
  if (!math) return stale ? "Scored under previous rules — re-screen to refresh." : "Click to open the breakdown";
  const base = math.base + " base " + (math.adjustments >= 0 ? "+" : "") + math.adjustments + " signals"
    + (math.preferences ? " " + (math.preferences >= 0 ? "+" : "") + math.preferences + " preference" : "")
    + " = " + math.final;
  return stale
    ? "Scored under previous rules — " + base + " · click to open the breakdown"
    : base + " · click to open the breakdown";
}

export function ScoreTip({ score, math, stale, size = 24, onOpenMath }: {
  score: number; math?: ScoreMathBrief | null; stale?: boolean; size?: number;
  onOpenMath?: () => void;
}) {
  return (
    <Tip content={scoreTipLine(math, !!stale)}>
      <button
        onClick={(e) => { e.stopPropagation(); onOpenMath?.(); }}
        aria-label="How this score adds up"
        style={{ display: "inline-flex", alignItems: "center", gap: 5, border: 0, background: "none", padding: 0, cursor: onOpenMath ? "pointer" : "default", fontFamily: "inherit" }}
      >
        {stale && <span style={{ color: "var(--warn-ink)", display: "inline-flex" }}><IconOutdated size={Math.round(size * 0.58)} /></span>}
        <span style={{ fontSize: size, fontWeight: 700, lineHeight: 1.2, color: stale ? "var(--ink-soft)" : "var(--brand-green-deep)", textDecoration: "underline dotted", textDecorationColor: "var(--border-strong)", textUnderlineOffset: 4 }}>
          {score}
        </span>
      </button>
    </Tip>
  );
}

function CandidateRow({
  card, isHM, stale, selected, active, flash = false, onToggleSel, onOpen, onOpenMath, onOpenComments, onReconfirm, onDecide, onTake, onStaleGate, readOnly = false, nameOf = (e) => e ?? "",
}: {
  card: ReviewCard;
  isHM: boolean;
  /** Resolves a decided_by address to a person's name. */
  nameOf?: (email: string | null | undefined) => string;
  /** Read-only viewer (Legal/audit): no checkbox, no decision/take controls. */
  readOnly?: boolean;
  /** This run's scores were produced under older rules — mark the score; a
   *  paused decision click opens the stale-gate confirm (the server 409s too). */
  stale: boolean;
  selected: boolean;
  active: boolean; // the j/k keyboard focus row — gets a visible ring
  /** One-shot highlight when a notification deep link targets this row. */
  flash?: boolean;
  onToggleSel: () => void;
  onOpen: () => void;
  onOpenMath: () => void;
  /** Open the drawer scrolled to the comment thread (comment-icon click). */
  onOpenComments: () => void;
  /** Reconfirm a decision carried over from scores that have since moved. */
  onReconfirm: () => void;
  onDecide: (id: string, action: string) => void;
  onTake: (id: string, take: Take) => void;
  onStaleGate: () => void;
}) {
  const dec = card.decided ? DECISION_STYLE[card.decided] : null;
  const gates = card.gates ?? { total: 0, failed: [] };
  const gateMissed = gates.failed.length > 0;
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (active) ref.current?.scrollIntoView?.({ block: "nearest" });
  }, [active]);
  return (
    <div
      ref={ref}
      id={`cand-${card.candidate_id}`}
      onClick={onOpen}
      className={"card-hover rev-card " + (card.band ? BAND_CLASS[card.band] ?? "" : "")}
      data-active={active ? "true" : undefined}
      style={{ background: "var(--white)", border: `1px solid ${selected ? "var(--brand-green)" : "var(--border-subtle)"}`, borderRadius: 14, padding: "12px 16px", boxShadow: "var(--shadow-sm)", cursor: "pointer", outline: active ? "2px solid var(--brand-green)" : undefined, outlineOffset: 1, animation: flash ? "brss-flash 1.4s cubic-bezier(0.2,0,0,1) 2" : undefined }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        {!readOnly && <Checkbox checked={selected} onClick={onToggleSel} />}
        {/* Rank over score, always (2026-09-07, item 5). The "#" is what makes the
            top number a POSITION rather than another score. */}
        {/* No verdict, no badge: without a score the position would only restate an
            order that is by decision and name, not by fit. */}
        {card.score != null && (
          <span className="rank-badge has-score" title={`Rank ${card.rank} · match score ${card.score} of 100`}>
            <span className="rank-n"><span className="rank-hash">#</span>{card.rank}</span>
            <span className="rank-score">{card.score}</span>
          </span>
        )}
        <div style={{ flex: "1 1 240px", minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
            <span style={{ fontSize: 15, fontWeight: 700, color: "var(--ink)" }}>{card.name}</span>
            {card.band_label && <Badge band={card.band}>{card.band_label}</Badge>}
            {!isHM && card.hm_take && <HmTakeChip take={card.hm_take.take} name={card.hm_take.hm_name} />}
            {gates.total > 0 && (() => {
              const passed = gates.total - gates.failed.length;
              const col = gateChipColor(passed, gates.total);
              return (
                <span
                  title={gates.failed.length ? `Hard gate(s) not met: ${gates.failed.join("; ")}` : "All hard gates met"}
                  style={{ fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 999, background: col.bg, color: col.fg }}
                >
                  Gates {passed}/{gates.total}
                </span>
              );
            })()}
            {card.flag && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11, fontWeight: 700, color: "var(--warn-ink)" }}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><path d="M12 9v4" /><path d="M12 17h.01" /></svg>
                Closer look
              </span>
            )}
            {dec && <span style={{ fontSize: 12, fontWeight: 700, color: dec.fg }}>· {dec.label}</span>}
            {card.decided_reconfirm && !isHM && !readOnly && (
              <button
                onClick={(e) => { e.stopPropagation(); onReconfirm(); }}
                title="This decision was made under previous scores that have since changed — click to reconfirm it on the current scores."
                style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 10.5, fontWeight: 700, padding: "2px 8px", borderRadius: 999, cursor: "pointer", fontFamily: "inherit", background: "var(--surface-warn)", border: "1px solid var(--warn-border)", color: "var(--warn-ink)" }}
              >
                <IconOutdated size={11} /> Reconfirm
              </button>
            )}
            {card.decided_reason && (
              <span
                title={`Note from ${card.decided_by ? nameOf(card.decided_by) : "a reviewer"}: “${card.decided_reason}”`}
                aria-label="Has a reviewer note"
                style={{ display: "inline-flex", alignItems: "center", color: "var(--accent-blue-deep, #16729E)" }}
              >
                <IconComment size={14} />
              </span>
            )}
            {(card.comments ?? 0) > 0 && (
              <button
                onClick={(e) => { e.stopPropagation(); onOpenComments(); }}
                title={`${card.comments} comment${card.comments === 1 ? "" : "s"} — open the thread`}
                style={{ display: "inline-flex", alignItems: "center", gap: 3, fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 999, cursor: "pointer", fontFamily: "inherit", border: "1px solid var(--accent-blue-tint)", background: "var(--accent-blue-tint)", color: "var(--info-ink)" }}
              >
                <IconComment size={12} /> {card.comments}
              </button>
            )}
          </div>
          {gateMissed && (
            // ONE line naming the gate(s) not met; the rule itself is said once, beside
            // the gate filter (GATE_RULE), instead of a paragraph on every card.
            <div title={`Not evidenced: ${gates.failed.join("; ")}. ${GATE_RULE}`}
                 style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, color: "var(--hue-amber-ink)", marginTop: 5, minWidth: 0 }}>
              <strong style={{ flex: "0 0 auto" }}>Not evidenced</strong>
              <span style={{ color: "var(--ink-soft)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                · {gates.failed.length === gates.total && gates.total > 1 ? `none of the ${gates.total} gates evidenced` : gates.failed.join("; ")}
              </span>
            </div>
          )}
          {card.location_note && (
            <div
              title={card.location_note}
              style={{ display: "flex", alignItems: "center", gap: 5, marginTop: 6, fontSize: 11.5, color: "var(--ink-muted)" }}
            >
              <IconPin size={12} style={{ flex: "0 0 auto" }} />
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {card.location_note}
              </span>
            </div>
          )}
          {(card.chips?.length ?? 0) > 0 && <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 7 }}>
            {(card.chips ?? []).map((chip, i) => (
              <span
                key={i}
                title={chip.text}
                style={{
                  display: "inline-block",
                  maxWidth: 240,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  fontSize: 12,
                  lineHeight: "18px",
                  padding: "2px 10px",
                  borderRadius: 999,
                  background: chip.kind === "strength" ? "var(--hue-green-tint)" : "var(--white)",
                  border: chip.kind === "strength" ? "1px solid transparent" : "1px solid var(--border-subtle)",
                  color: chip.kind === "strength" ? "var(--hue-green-ink)" : "var(--ink-soft)",
                }}
              >
                {chip.text}
              </span>
            ))}
          </div>}
          {card.decided_reason && (
            <div
              title={card.decided_by ? `${card.decided_reason} — ${nameOf(card.decided_by)}` : card.decided_reason}
              style={{ marginTop: 5, fontSize: 12.5, color: "var(--ink-soft)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 620 }}
            >
              Noted: “{card.decided_reason}”
              {card.decided_by && <span style={{ color: "var(--ink-muted)" }}> — {nameOf(card.decided_by)}</span>}
            </div>
          )}
        </div>
        <div style={{ flex: "0 0 auto", marginLeft: "auto", display: "flex", alignItems: "center", gap: 14 }}>
          {card.score != null && (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", width: 52 }}
                 onClick={(e) => e.stopPropagation()}>
              {/* the design score: hover = the one-line math, click = the drawer math */}
              <ScoreTip score={card.score} math={card.math} stale={stale} onOpenMath={onOpenMath} />
              <span style={{ fontSize: 10, color: "var(--ink-soft)", marginTop: 2 }}>/ 100</span>
            </div>
          )}
          {readOnly ? null : isHM ? (
            <HmTakeButtons value={card.hm_take?.take ?? null} onPick={(t) => onTake(card.candidate_id, t)} />
          ) : (
            <div style={{ display: "flex", gap: 7 }} onClick={(e) => e.stopPropagation()}
                 title={stale ? "Paused — the rules changed since this screen ran. Re-screen to decide on current scores." : undefined}>
              {/* clicking the active decision again clears it (back to undecided);
                  clearing stays possible on a stale run — a paused NEW decision
                  opens the design "ground shifted" confirm instead */}
              <MiniBtn active={card.decided === "forward"} kind="forward" onClick={() => (stale && card.decided !== "forward" ? onStaleGate() : onDecide(card.candidate_id, card.decided === "forward" ? "clear" : "forward"))}>Move forward</MiniBtn>
              <MiniBtn active={card.decided === "discuss"} kind="discuss" onClick={() => (stale && card.decided !== "discuss" ? onStaleGate() : onDecide(card.candidate_id, card.decided === "discuss" ? "clear" : "discuss"))}>Discuss</MiniBtn>
              <MiniBtn active={card.decided === "not"} kind="not" onClick={() => (stale && card.decided !== "not" ? onStaleGate() : onDecide(card.candidate_id, card.decided === "not" ? "clear" : "not"))}>Not a fit</MiniBtn>
            </div>
          )}
          <span style={{ color: "var(--ink-muted)", display: "inline-flex" }}><IconChevronRight size={18} /></span>
        </div>
      </div>
    </div>
  );
}

export function MiniBtn({ active, kind, onClick, disabled = false, emphasis = false, children }: {
  active: boolean; kind: string; onClick: () => void; disabled?: boolean;
  /** Lead with this one (the bulk bar's Move forward): filled in its hue without
   *  claiming to be the current decision. */
  emphasis?: boolean;
  children: React.ReactNode;
}) {
  const s = DECISION_STYLE[kind];
  const lit = active || emphasis;
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        height: 32,
        padding: "0 12px",
        borderRadius: 999,
        border: lit ? `1px solid ${s.fg}` : "1px solid var(--border-subtle)",
        background: lit ? s.bg : "var(--white)",
        color: lit ? s.fg : "var(--ink-soft)",
        fontFamily: "inherit",
        fontSize: 12.5,
        fontWeight: 700,
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.45 : 1,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </button>
  );
}
