// The candidate drawer (2026-09-06 restructure, pilot feedback item 15).
//
// It used to be one long scroll: a header that repeated the card, a score card, a
// decision bar, a 380px résumé window, the gate/flag banners, the verdict,
// strengths, watch-outs, the checklist, the AI adjustments, the preferences, a
// private note, the comments, the math. Recruiters saw a quarter of the résumé and
// missed the evidence entirely. Now: a COMPACT header (name · band · score · your
// call · decision note), then four sections a click apart, each with the whole
// height to itself:
//   Evidence  — the verdict, the gate/flag banners, strengths, watch-outs, the
//               checklist with every quote (the default)
//   Résumé    — the original, full height
//   Score     — how the number was built: summary, AI adjustments, preferences, math
//   Notes     — the note to self and the comment thread (@-mentions from the directory)
// Deep links land on the right section: a score click opens Score with the math
// unfolded; a comment notification opens Notes on that comment.
import { useEffect, useRef, useState } from "react";
import { Badge, Callout, InfoDot, Spinner, Tip } from "../components/ui";
import { keepIfSame, useHiddenAwarePoll } from "../hooks";
import { ConfirmModal } from "../components/overlays";
import { IconX, IconCheck, IconComment, IconPin, IconAlert, IconChevronDown, IconChevronRight } from "../components/icons";
import { MiniBtn, HmTakeChip, ScoreTip } from "./workspace/ReviewTab";
import HmTakeButtons, { type Take } from "../components/HmTakeButtons";
import { ScoreMathSection, ScoreSummaryCard } from "../components/ScoreMathPanel";
import { get, post, put, getPolled } from "../api";
import { useMe } from "../MeContext";
import { Avatar, usePeople, personName } from "../people";
import type { CandidateComment, CandidateDetail, Participant, PeopleSearch, ReactionView, ReqSpan, ScoreMath, ScoreMathBrief } from "../types";
import { formatDistance, unitsFor } from "../lib/units";

// The fixed reaction palette (mirrors comments.ALLOWED_REACTIONS on the server).
const REACTIONS = ["👍", "❤️", "🎉", "👀", "🙌", "😄"];
const COMMENT_POLL_MS = 6000;

// The popover on the drawer's score reuses the compact shape, derived from the
// full trace so the two can never disagree.
function briefFromMath(m: ScoreMath): ScoreMathBrief {
  return {
    base: m.base.total,
    adjustments: m.adjustments.net,
    suspended_count: m.adjustments.suspended.length,
    preferences: m.preferences.net,
    before_limits: m.total.before_limits,
    final: m.total.final,
    clamped: m.total.clamped,
    band_label: m.band.label,
  };
}

// The drawer's minimum width — dragging only widens.
export const DRAWER_MIN = 560;
// The DEFAULT width (2026-09-07, was 680): the evidence list is the drawer's real
// job and 680 wrapped almost every requirement onto three lines. 880 fits the
// strengths/watch-outs pair side by side, gives the résumé viewer a readable page,
// and still leaves the ranked list's rank + score column showing at 1440. Dragging
// still overrides it, per requisition.
export const DRAWER_DEFAULT = 880;

/** Clamp a requested width to [min(DRAWER_MIN, 88vw), 88vw]. Pure, so the resize
 *  math is unit-testable without a layout engine. */
export function clampDrawerWidth(w: number, viewport: number): number {
  const max = Math.round(viewport * 0.88);
  return Math.max(Math.min(DRAWER_MIN, max), Math.min(w, max));
}

const TA_DECISION_LABEL: Record<string, string> = {
  forward: "Move forward",
  discuss: "Discuss",
  not: "Not a fit",
};

const MATCH_COLOR: Record<string, string> = { full: "var(--hue-green-ink)", partial: "var(--hue-amber-ink)", none: "var(--ink-soft)" };

/** What ✓ ~ ✗ mean, on hover (2026-09-11). A tick explains itself; "~" does not, and
 *  a reviewer who reads it as "no" treats a part-way match as a rejection. */
export const MATCH_MEANING: Record<string, string> = {
  full: "Met — the résumé shows this",
  partial: "Partly met — the résumé shows some of it, not all. Your call",
  none: "Not found — the résumé gives nothing to judge this by",
};

type Section = "evidence" | "resume" | "score" | "notes";

/** What a "closer look" reason MEANS, in words a recruiter uses, and what to do
 *  about it (2026-09-07). The server sends the reason and where it points; the
 *  wording that explains the machinery belongs here, next to the reader. `where` is
 *  the drawer section the reason concerns. */
const FLAG_HELP: Record<string, { title: string; what: string; do: string }> = {
  verdict: {
    title: "About the AI's summary",
    what: "The short summary at the top is written by the AI. This one used wording that "
      + "can quietly stand in for something we never score — things like age, or gaps "
      + "between jobs. The wording is all that was flagged: the score itself does not use "
      + "any of that.",
    do: "Read the summary and decide whether it describes the person fairly. If it reads "
      + "oddly, ignore it and judge from the checklist and the résumé.",
  },
  score: {
    title: "About the extra points",
    what: "Beyond the checklist, the AI can add or take away a few points for things like "
      + "depth in a tool or a run of relevant work. A fairness check reviews each one. "
      + "Some here were either set aside — removed from the score — or kept but marked "
      + "as thin reasoning.",
    do: "Open Score and read them. Each says what it was for and quotes the résumé, so "
      + "you can see whether you agree.",
  },
  resume: {
    title: "About the reading quality",
    what: "Parts of this file were hard to read — often a scan, a photo, or heavy "
      + "formatting. The AI still judged it, but it had less to work with, so a match "
      + "may be thinner than it looks.",
    do: "Open the résumé and check the parts that matter to you before deciding.",
  },
  evidence: {
    title: "About the evidence",
    what: "Something here needs a person rather than a score: either the résumé shows "
      + "part of what a requirement asks, or the AI judged a requirement met but could "
      + "not point at the exact words to prove it.",
    do: "Open the checklist below, read what the résumé actually says for that line, and "
      + "make the call yourself.",
  },
};
type ReqView = "all" | "gate" | "must" | "nice" | "gaps";

/** Which of the four checklist views a row belongs to. A gate row is only a gate
 *  (the server flags `must` and `gate` exclusively); "gaps" is anything not fully met. */
function reqGroup(r: { must: boolean; gate: boolean }): "gate" | "must" | "nice" {
  return r.gate ? "gate" : r.must ? "must" : "nice";
}
const REQ_VIEW_LABEL: Record<Exclude<ReqView, "all" | "gaps">, string> = { gate: "Gates", must: "Must-have", nice: "Nice-to-have" };

// What the AI adjustments ARE, said once above them (item 16). Not "rewarded": an
// adjustment can deduct as well as add, and a set-aside one counts for nothing.
const ADJUSTMENTS_EXPLAINER = "Beyond the checklist, the AI added or deducted a few points for each of these. "
  + "Every one is capped, backed by a quote from the résumé and checked by the fairness critic; "
  + "a set-aside one is shown but does not count.";

export default function CandidateDrawer({
  reqId,
  candidateId,
  blind,
  takeMode = false,
  readOnly = false,
  frozen = false,
  taName = "the recruiter",
  pack = null,
  stale = false,
  focusMath = false,
  focusComment = null,
  width = null,
  onWidthChange,
  onBackdropWheel,
  backTo,
  onClose,
  onDecide,
  onTake,
  onStaleGate,
  onCommented,
  onReconfirmed,
}: {
  reqId: string;
  candidateId: string;
  blind: boolean;
  /** P42: hiring-manager view — record a take, not a decision. */
  takeMode?: boolean;
  /** Read-only viewer (Legal/audit): full detail, no decision/note/comment writes. */
  readOnly?: boolean;
  /** A closed requisition: no decisions, takes or reconfirms; notes and comments stay. */
  frozen?: boolean;
  taName?: string;
  /** The requisition's hiring-rules pack, which decides the DISPLAY unit for a
   *  distance: a Californian recruiter thinks in miles. Kilometres stay stored
   *  and measured (2026-09-12). */
  pack?: string | null;
  /** P51: this run's scores were produced under older rules — mark the score,
   *  pause new decisions (the server 409s them anyway). */
  stale?: boolean;
  /** Opened from a score click — land on the Score section with the math open. */
  focusMath?: boolean;
  /** Opened from a comment notification — land on that comment ("latest" =
   *  the newest one) and flash it. */
  focusComment?: string | null;
  /** A paused decision click opens the workspace's stale-gate confirm. */
  onStaleGate?: () => void;
  /** Persisted drawer width (px); null = the default min(560px, 88%). */
  width?: number | null;
  onWidthChange?: (w: number | null) => void;
  /** A wheel turn over the dimmed list beside the drawer (item 10): the tab scrolls
   *  its list by this much, so the pointer's position decides what scrolls. */
  onBackdropWheel?: (deltaY: number) => void;
  /** Where this drawer was opened FROM (the Shortlist's second pass, 2026-09-07):
   *  rendered inside the drawer, because the drawer is what the reader opened. */
  backTo?: { label: string; go: () => void };
  onClose: () => void;
  /** Resolves false when the decision failed to stick, so the drawer reverts. */
  onDecide: (id: string, action: string, reason?: string) => Promise<boolean>;
  onTake?: (id: string, take: Take) => Promise<void> | void;
  /** A comment was added — the list bumps its 💬 count without a refetch. */
  onCommented?: (id: string) => void;
  /** A carried-forward decision was reconfirmed — the card clears its badge. */
  onReconfirmed?: (id: string) => void;
}) {
  const people = usePeople();
  const [detail, setDetail] = useState<CandidateDetail | null>(null);
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);
  const mathRef = useRef<HTMLDivElement>(null); // score click lands here
  const notesRef = useRef<HTMLDivElement>(null); // "jump to the adjustments" anchor
  const paneRef = useRef<HTMLDivElement>(null);          // the drawer's scroll region
  const verdictRef = useRef<HTMLDivElement>(null);       // "Read the summary" lands here
  const checklistRef = useRef<HTMLDivElement>(null);     // "Covers N%" lands here
  // The requirement row a link asked us to open: scrolled to, expanded and ringed
  // (2026-09-07 — "partial evidence · your call" and the watch-out chips were dead
  // text; the evidence they refer to was three scrolls away).
  const [aimRow, setAimRow] = useState<number | null>(null);
  // The "closer look" explainer (item 4): a short dialog in plain words, rather than
  // a jump to a line that explained nothing.
  const [helpFor, setHelpFor] = useState<{ where: string; text: string } | null>(null);
  const [take, setTake] = useState<Take | null>(null); // optimistic take highlight
  const [reason, setReason] = useState("");
  const [savedReason, setSavedReason] = useState(""); // the note currently on the record
  const [openEvidence, setOpenEvidence] = useState<Record<number, boolean>>({});
  const [localDec, setLocalDec] = useState<string | null>(null); // null=untouched, "none"=cleared
  const [section, setSection] = useState<Section>("evidence");
  // The full arithmetic is OPEN by default on the Score section (item 11: the score
  // has a section of its own now, so there is nothing to protect by folding it);
  // "Hide the math" folds it for the reader who only wants the summary.
  const [showMath, setShowMath] = useState(true);
  // The checklist's view switch (item 8): all rows, or one kind, or the gaps.
  const [reqViewRaw, setReqView] = useState<ReqView>("all");
  const reqView = reqViewRaw;
  // Switching to a shorter view makes the pane taller than its content, so the
  // browser clamps the scroll and everything jumps (2026-09-07, item 4). Pin the
  // checklist to the top of the pane on every switch: the rows change, the frame
  // does not.
  useEffect(() => {
    if (!checklistRef.current) return;
    // Two frames: the first lets React's commit lay the new rows out, the second
    // measures a settled page. One frame measured the OLD height and landed short.
    let inner = 0;
    const id = window.requestAnimationFrame(() => {
      inner = window.requestAnimationFrame(() => scrollPaneTo(checklistRef.current, 0));
    });
    return () => { window.cancelAnimationFrame(id); window.cancelAnimationFrame(inner); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reqViewRaw]);
  // The collaboration thread (2026-07-19): comments carry their OWN author.
  const [comments, setComments] = useState<CandidateComment[]>([]);
  const [flashCommentId, setFlashCommentId] = useState<string | null>(null);
  // @-mention roster (recruiting team + assigned HM) and reconfirm state.
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [reconfirming, setReconfirming] = useState(false);

  useEffect(() => {
    setDetail(null);
    setLocalDec(null);
    setTake(null);
    setComments([]);
    setOpenEvidence({});
    setReqView("all");
    setSection(focusMath ? "score" : focusComment ? "notes" : "evidence");
    get<CandidateDetail>(`/api/roles/${reqId}/review/${candidateId}?blind=${blind}`).then((d) => {
      setDetail(d);
      // Without verdict_view the server sends no evidence or score (InfoSec,
      // 2026-10-14) — open on the résumé instead of an empty section.
      if (d.score == null) setSection(focusComment ? "notes" : "resume");
      setTake(d.hm_take?.take ?? null);
      // Seed the note input from the recorded decision so a re-decide keeps it.
      setReason(d.decision?.reason ?? "");
      setSavedReason(d.decision?.reason ?? "");
      setShowMath(true);
      setComments(d.comments ?? []);
      if (focusMath) {
        window.setTimeout(() => mathRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" }), 80);
      }
      if (focusComment) {
        const list = d.comments ?? [];
        const target = focusComment === "latest"
          ? list[list.length - 1]?.comment_id
          : list.some((c) => c.comment_id === focusComment) ? focusComment : list[list.length - 1]?.comment_id;
        if (target) {
          setFlashCommentId(target);
          window.setTimeout(() => {
            document.getElementById(`comment-${target}`)?.scrollIntoView?.({ behavior: "smooth", block: "center" });
          }, 120);
          window.setTimeout(() => setFlashCommentId(null), 3200);
        }
      }
    });
  }, [reqId, candidateId, blind, focusMath, focusComment]);

  const pickTake = (t: Take) => {
    setTake(t);
    void onTake?.(candidateId, t);
  };

  // Optimistic decision highlight; clicking the active call again clears it.
  // On failure the highlight + saved note revert to what they were.
  const decided = localDec !== null ? (localDec === "none" ? null : localDec) : (detail?.decision?.action ?? null);
  const choose = (action: string) => {
    const prevDec = localDec;
    const prevSaved = savedReason;
    const clearing = decided === action;
    setLocalDec(clearing ? "none" : action);
    setSavedReason(clearing ? "" : reason.trim());
    void onDecide(candidateId, clearing ? "clear" : action, reason).then((ok) => {
      if (!ok) {
        setLocalDec(prevDec);
        setSavedReason(prevSaved);
      }
    });
  };

  // Near-real-time comments (2026-07-20): poll the lightweight comments endpoint
  // while the drawer is open, so the other side's comments + reactions appear
  // within a few seconds — no manual "send updates".
  useHiddenAwarePoll(async () => {
    const r = await getPolled<{ comments: CandidateComment[] }>(
      `/api/roles/${reqId}/candidates/${candidateId}/comments`);
    setComments(keepIfSame(r.comments ?? []));
  }, COMMENT_POLL_MS, [reqId, candidateId]);

  // The @-mention roster (once per candidate) — the people already on this
  // requisition. The composer widens the search to the whole directory as you type.
  useEffect(() => {
    let live = true;
    get<{ participants: Participant[] }>(`/api/roles/${reqId}/participants`)
      .then((r) => { if (live) setParticipants(r.participants ?? []); })
      .catch(() => { /* no roster — mentions just won't autocomplete */ });
    return () => { live = false; };
  }, [reqId, candidateId]);

  // Toggle a reaction on a comment and fold the fresh map back into state.
  async function react(commentId: string, emoji: string) {
    try {
      const r = await post<{ comment_id: string; reactions: CandidateComment["reactions"] }>(
        `/api/roles/${reqId}/candidates/${candidateId}/comments/${commentId}/react`, { emoji });
      setComments((prev) => prev.map((c) =>
        c.comment_id === commentId ? { ...c, reactions: r.reactions } : c));
    } catch { /* transient */ }
  }

  const needsReconfirm = !takeMode && !!detail?.decision?.needs_reconfirm && localDec === null;
  async function reconfirm() {
    if (reconfirming) return;
    setReconfirming(true);
    try {
      await post(`/api/roles/${reqId}/candidates/${candidateId}/reconfirm`);
      setDetail((d) => (d && d.decision ? { ...d, decision: { ...d.decision, needs_reconfirm: false } } : d));
      onReconfirmed?.(candidateId);
    } catch { /* leave the badge; a retry is safe */ }
    finally { setReconfirming(false); }
  }

  // --- resize (F7): drag the left edge to widen; keyboard + double-click too. ---
  const setW = (w: number | null) =>
    onWidthChange?.(w === null ? null : clampDrawerWidth(w, window.innerWidth));
  const startDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    dragRef.current = { startX: e.clientX, startW: width ?? DRAWER_MIN };
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* jsdom */ }
    e.currentTarget.setAttribute("data-dragging", "true");
    document.body.style.userSelect = "none";
  };
  const onDragMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    // right-anchored panel: dragging left (clientX shrinks) widens it
    setW(dragRef.current.startW + (dragRef.current.startX - e.clientX));
  };
  const endDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    dragRef.current = null;
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* jsdom */ }
    e.currentTarget.removeAttribute("data-dragging");
    document.body.style.userSelect = "";
  };
  const keyResize = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "ArrowLeft") { setW((width ?? DRAWER_MIN) + 24); e.preventDefault(); }
    else if (e.key === "ArrowRight") { setW((width ?? DRAWER_MIN) - 24); e.preventDefault(); }
    else if (e.key === "Home") { setW(null); e.preventDefault(); }
  };
  // restore body userSelect if the drawer unmounts mid-drag
  useEffect(() => () => { document.body.style.userSelect = ""; }, []);


  /** Scroll the drawer's own pane so `el` sits `pad` px below its top. Computed,
   *  not scrollIntoView: the browser skips smooth scrolling while the panel is not
   *  painting, which turned every in-drawer jump into a silent no-op. */
  const scrollPaneTo = (el: HTMLElement | null, pad = 8) => {
    const pane = paneRef.current;
    if (!pane || !el) return;
    const top = el.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop;
    // scrollTo is absent in jsdom and can be missing on an unattached node; the
    // assignment below is what actually lands us there, so the smooth pass is optional.
    pane.scrollTo?.({ top: Math.max(0, top - pad), behavior: "smooth" });
    pane.scrollTop = Math.max(0, top - pad); // and land there even if smooth is ignored
  };

  // Land on something inside the Evidence section: switch to it, scroll, flash.
  const goTo = (ref: { current: HTMLElement | null }) => {
    setSection("evidence");
    window.setTimeout(() => {
      scrollPaneTo(ref.current);
      ref.current?.classList.add("aim-flash");
      window.setTimeout(() => ref.current?.classList.remove("aim-flash"), 1600);
    }, 60);
  };

  /** Open one checklist requirement by its TEXT (what a watch-out chip carries) or
   *  index: the checklist view widens to All so the row is definitely in it. */
  const goToRequirement = (find: (r: { label: string }) => boolean) => {
    const i = (detail?.reqs ?? []).findIndex(find);
    if (i < 0) return goTo(checklistRef);
    setSection("evidence");
    setReqView("all");
    setOpenEvidence((o) => ({ ...o, [i]: true }));
    setAimRow(i);
    window.setTimeout(() => {
      scrollPaneTo(document.getElementById(`req-row-${i}`), 52); // clear the sticky header
    }, 80);
    window.setTimeout(() => setAimRow(null), 2000);
  };

  const goToAdjustments = () => {
    setSection("score");
    window.setTimeout(() => scrollPaneTo(notesRef.current), 60);
  };
  const openMathAt = (id?: string) => {
    setSection("score");
    setShowMath(true);
    window.setTimeout(() => {
      scrollPaneTo(id ? document.getElementById(id) : mathRef.current);
    }, 60);
  };

  const coveredPct = (() => {
    const m = /Covers (\d+)%/.exec(detail?.sub ?? "");
    return m ? Number(m[1]) : null;
  })();
  const resumeHref = `/api/roles/${reqId}/candidates/${candidateId}/resume`;
  const brief = detail?.score_math ? briefFromMath(detail.score_math) : null;
  const notesCount = comments.length + (detail?.self_note?.text ? 1 : 0);
  // Did the AI's verdict arrive? Only for verdict_view holders (HR roles); for
  // everyone else the drawer is the résumé, the decision and the conversation.
  const verdictShown = detail?.score != null;

  return (
    <div style={{ position: "absolute", inset: 0, zIndex: 50 }}>
      {/* The dimmed list stays SCROLLABLE under the pointer (item 10): a wheel turn
          here is forwarded to the list; over the drawer, the drawer scrolls itself. */}
      <div onClick={onClose} onWheel={(e) => onBackdropWheel?.(e.deltaY)}
           style={{ position: "absolute", inset: 0, background: "rgba(20,22,26,0.4)" }} />
      <div
        className="rise"
        style={{
          position: "absolute",
          top: 0,
          right: 0,
          bottom: 0,
          width: width != null ? clampDrawerWidth(width, window.innerWidth) : `min(${DRAWER_DEFAULT}px, 88%)`,
          background: "var(--white)",
          boxShadow: "-10px 0 34px rgba(34,34,34,.16)",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* left-edge resize handle */}
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize panel"
          tabIndex={0}
          className="drawer-resize"
          onPointerDown={startDrag}
          onPointerMove={onDragMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onKeyDown={keyResize}
          onDoubleClick={() => setW(null)}
          style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 8, cursor: "col-resize", touchAction: "none", zIndex: 1 }}
        />
        {helpFor && (() => {
          const help = FLAG_HELP[helpFor.where] ?? FLAG_HELP.evidence;
          const act = { score: "Open Score", resume: "Open the résumé",
                        verdict: "Show me the summary" }[helpFor.where] ?? "Show me the checklist";
          return (
            <ConfirmModal
              spec={{
                title: help.title,
                reason: helpFor.text,
                question: (
                  <>
                    <p style={{ margin: "0 0 8px" }}>{help.what}</p>
                    <p style={{ margin: 0 }}><strong>What to do:</strong> {help.do}</p>
                  </>
                ),
                cta: act,
                onProceed: () => {
                  const where = helpFor.where;
                  setHelpFor(null);
                  if (where === "score") goToAdjustments();
                  else if (where === "resume") setSection("resume");
                  else if (where === "verdict") goTo(verdictRef);
                  else goTo(checklistRef);
                },
              }}
              onCancel={() => setHelpFor(null)}
            />
          );
        })()}
        {!detail ? (
          <div style={{ display: "flex", justifyContent: "center", padding: 60 }}><Spinner /></div>
        ) : (
          <>
            {/* ---- compact header: who · band · score · your call · note ---- */}
            <div style={{ flex: "0 0 auto", padding: "14px 22px 10px", borderBottom: "1px solid var(--border-subtle)" }}>
              {backTo && (
                <button className="drawer-back" onClick={backTo.go}>‹ {backTo.label}</button>
              )}
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <div style={{ flex: "1 1 auto", minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
                    <h2 style={{ fontSize: 19, margin: 0, lineHeight: 1.2 }}>{detail.name}</h2>
                    {detail.band_label && <Badge band={detail.band}>{detail.band_label}</Badge>}
                    {!takeMode && detail.hm_take && <HmTakeChip take={detail.hm_take.take} name={detail.hm_take.hm_name} />}
                  </div>
                  {/* The first thing a recruiter checks about a person (item 7). Shown,
                      never scored — commute/address is a floor never-scored topic. */}
                  {(detail.location_stated || detail.location_note) && (
                    <div style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12.5, color: "var(--hue-blue-ink)", marginTop: 4 }}
                         title={detail.location_note || undefined}>
                      <IconPin size={13} />
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {detail.location_stated
                          ? <>{detail.location_stated}{detail.location_km != null && <span style={{ color: "var(--ink-muted)" }}> · {formatDistance(detail.location_km, unitsFor(pack))} from {detail.location_place}</span>}</>
                          : "No location on the résumé"}
                      </span>
                    </div>
                  )}
                  {/* "Covers N% of the checklist" is the headline number for the whole
                      Evidence section, so it takes you there (item 3). */}
                  {detail.sub && (
                    <div style={{ fontSize: 12.5, color: "var(--ink-soft)", marginTop: 3 }}>
                      <button className="sub-link" onClick={() => goTo(checklistRef)}
                              title="Show the screening checklist">
                        {detail.sub}
                      </button>
                    </div>
                  )}
                </div>
                {verdictShown && (
                  <div style={{ flex: "0 0 auto", textAlign: "right" }} onClick={(e) => e.stopPropagation()}>
                    <ScoreTip score={detail.score ?? 0} math={brief} stale={stale} size={28} onOpenMath={() => openMathAt()} />
                    <div style={{ fontSize: 10, color: "var(--ink-soft)", marginTop: -2 }}>/ 100</div>
                  </div>
                )}
                <button onClick={onClose} aria-label="Close" style={{ flex: "0 0 auto", width: 32, height: 32, borderRadius: "50%", border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink-soft)", cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
                  <IconX size={15} />
                </button>
              </div>

              {/* the call — a take (HM) or a decision (recruiter), on ONE row with its note */}
              {takeMode ? (
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 12.5, fontWeight: 700 }}>Your take:</span>
                  {frozen
                    ? <span style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>Closed — takes are frozen</span>
                    : <HmTakeButtons value={take} onPick={pickTake} />}
                  {detail.decision && (
                    <span style={{ fontSize: 12, color: "var(--ink-soft)", marginLeft: "auto" }}>
                      {taName}: <strong style={{ color: "var(--ink)" }}>{TA_DECISION_LABEL[detail.decision.action] ?? detail.decision.action}</strong>
                      {detail.decision.reason && ` — “${detail.decision.reason}”`}
                    </span>
                  )}
                </div>
              ) : !readOnly && !frozen && detail.decisions_visible !== false ? (
                <div className="call-bar"
                     title={stale ? "Paused — the rules changed since this screen ran. Re-screen to decide on current scores." : undefined}>
                  <span style={{ fontSize: 12.5, fontWeight: 800, marginRight: 2 }}>Your call:</span>
                  <MiniBtn active={decided === "forward"} kind="forward" onClick={() => (stale && decided !== "forward" ? onStaleGate?.() : choose("forward"))}>Move forward</MiniBtn>
                  <MiniBtn active={decided === "discuss"} kind="discuss" onClick={() => (stale && decided !== "discuss" ? onStaleGate?.() : choose("discuss"))}>Discuss</MiniBtn>
                  <MiniBtn active={decided === "not"} kind="not" onClick={() => (stale && decided !== "not" ? onStaleGate?.() : choose("not"))}>Not a fit</MiniBtn>
                  {/* the decision note travels WITH the call and the team sees it — the
                      private note-to-self lives under Notes, and the two are labelled
                      so they can never be mistaken for each other (item 15) */}
                  <input className="input" style={{ flex: "1 1 200px", minWidth: 160, height: 32, fontSize: 12.5 }}
                         aria-label="Decision note (shared with the team)"
                         placeholder="Decision note — the team sees this (optional)…"
                         value={reason} onChange={(e) => setReason(e.target.value)} />
                </div>
              ) : detail.decisions_note ? (
                // Outside the requisition's team (2026-10-21): say why there is no call
                // here, rather than a blank where the buttons were.
                <div style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 10 }}>{detail.decisions_note}</div>
              ) : null}
              {!takeMode && decided && savedReason && (
                <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 6 }}>
                  Noted: “{savedReason}”
                  {detail.decision?.reviewer && (
                    <span style={{ color: "var(--ink-muted)" }}> — {personName(detail.decision.reviewer, people)}</span>
                  )}
                </div>
              )}

              {/* the section switcher */}
              <div className="seg-tabs" role="tablist" aria-label="Candidate sections" style={{ marginTop: 12 }}>
                {([
                  ["evidence", "Evidence", detail.req_summary ?? null],
                  ["resume", "Résumé", null],
                  ["score", "Score", null],
                  ["notes", "Notes & comments", notesCount ? String(notesCount) : null],
                ] as [Section, string, string | null][])
                  .filter(([key]) => verdictShown || (key !== "evidence" && key !== "score"))
                  .map(([key, label, count]) => (
                  <button key={key} role="tab" aria-selected={section === key} className={section === key ? "is-on" : ""}
                          onClick={() => setSection(key)}>
                    {label}{count && <span className="seg-count">{count}</span>}
                  </button>
                ))}
              </div>
            </div>

            {/* ---- the open section, full height ---- */}
            <div ref={paneRef} style={{ flex: "1 1 auto", minHeight: 0, overscrollBehavior: "contain",
                          ...(section === "resume"
                            // overflowY AUTO, not hidden (2026-09-11): when the drawer is
                            // shorter than the viewer's floor the bottom of the résumé used
                            // to be clipped with nothing to scroll. The bar above it is
                            // sticky, so the way out — "Open in a new tab" — stays on screen
                            // however far down the page you are.
                            ? { display: "flex", flexDirection: "column", overflowY: "auto", padding: 0 }
                            : { overflowY: "auto", padding: "16px 22px 40px" }) }}>
              {section === "evidence" && verdictShown && (
                <>
                  {needsReconfirm && !readOnly && !frozen && (
                    <div style={{ marginBottom: 12 }}>
                      <Callout variant="warn" title="Decided under previous scores">
                        The scores changed since this decision was made. Reconfirm to carry it onto the current scores — your note is kept.
                        <div style={{ marginTop: 8 }}>
                          <button
                            onClick={() => void reconfirm()}
                            disabled={reconfirming}
                            style={{ height: 32, padding: "0 14px", borderRadius: 8, border: "1px solid var(--brand-green)", background: "var(--brand-green-tint)", color: "var(--brand-green-deep)", fontFamily: "inherit", fontSize: 12.5, fontWeight: 700, cursor: reconfirming ? "default" : "pointer", opacity: reconfirming ? 0.6 : 1 }}
                          >
                            {reconfirming ? "Reconfirming…" : "Reconfirm decision"}
                          </button>
                        </div>
                      </Callout>
                    </div>
                  )}

                  {/* The gate block, one line per unmet requirement (item 4). It was a
                      paragraph that ran three gate texts, the rule and the caveat
                      together; the rule is now said once, at the bottom, and PARTIAL
                      evidence is marked as the case that needs a person. */}
                  {detail.gate_warn && (
                    <div className="gate-block">
                      <div className="gate-head">
                        <IconAlert size={15} />
                        {detail.gate_warn}
                      </div>
                      <ul className="gate-list">
                        {(detail.gate_items ?? []).map((g, i) => (
                          <li key={i}>
                            <span>{g.text}</span>
                            <button
                              className={"gate-tag is-link" + (g.state === "none" ? "" : " gate-tag-partial")}
                              onClick={() => goToRequirement((r) => r.label === g.text)}
                              title={g.state === "unverified"
                                ? "The AI read this as met but couldn't point at the words — open the résumé and decide"
                                : g.state === "partial"
                                  ? "Show what the résumé did say for this requirement"
                                  : "Show this requirement in the checklist"}
                            >
                              {g.state === "unverified" ? "AI says met · unproven · your call"
                                : g.state === "partial" ? "part-way met · your call"
                                : "nothing found →"}
                            </button>
                          </li>
                        ))}
                      </ul>
                      {detail.gate_rule && <div className="gate-rule">{detail.gate_rule}</div>}
                    </div>
                  )}

                  {/* Every "closer look" reason with an action (item 9): the critic’s own
                      sentence told nobody what to do about it. */}
                  {detail.flag && (detail.flag_items?.length ?? 0) > 0 && (
                    <div className="flag-block">
                      <div className="flag-head">Worth a closer look</div>
                      <ul className="flag-list">
                        {(detail.flag_items ?? []).map((f, i) => (
                          <li key={i}>
                            <span>{f.text}</span>
                            <button
                              onClick={() => setHelpFor({ where: f.where, text: f.text })}
                              className="flag-action"
                            >
                              What does this mean?
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {detail.flag && !(detail.flag_items?.length ?? 0) && detail.flag_note && (
                    <div style={{ marginBottom: 12 }}>
                      <Callout variant="warn" title="Worth a closer look">{detail.flag_note}</Callout>
                    </div>
                  )}

                  <div ref={verdictRef} style={{ margin: "0 0 14px", scrollMarginTop: 8, borderRadius: 8 }}>
                    <ScoreLine math={detail.score_math ?? null} verdict={detail.verdict ?? ""}
                               onChecklist={() => goTo(checklistRef)} onAdjustments={goToAdjustments} />
                  </div>

                  {((detail.strengths ?? []).length > 0 || (detail.watchouts ?? []).length > 0) && (
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12, marginBottom: 16 }}>
                      {(detail.strengths ?? []).length > 0 && (
                        <div>
                          <SectionHead title="Strengths" count={(detail.strengths ?? []).length} />
                          <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                            {(detail.strengths ?? []).map((s, i) => (
                              <Line key={i} bg="var(--hue-green-tint)"
                                    icon={<IconCheck size={14} style={{ color: "var(--hue-green-ink)" }} />}
                                    onClick={() => goToRequirement((r) => r.label === s)}>
                                {s}
                              </Line>
                            ))}
                          </div>
                        </div>
                      )}
                      {(detail.watchouts ?? []).length > 0 && (
                        <div>
                          <SectionHead title="Watch-outs" count={(detail.watchouts ?? []).length} />
                          <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                            {(detail.watchouts ?? []).map((w, i) => (
                              <Line key={i} bg="var(--hue-amber-tint)"
                                    icon={<span style={{ color: "var(--hue-amber-ink)", fontWeight: 700 }}>!</span>}
                                    onClick={() => goToRequirement((r) => r.label === w)}>
                                {w}
                              </Line>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* checklist with evidence — every row, the quotes one click away. The
                      view switches (item 8) narrow it to one kind, or to the gaps; each
                      carries its own met/total so the header answers "how did they do on
                      the must-haves?" before a row is read. */}
                  {(() => {
                    const met = (r: { match: string }) => r.match === "full";
                    const rows = (detail.reqs ?? []).map((r, i) => ({ r, i }));
                    const counts = (["gate", "must", "nice"] as const).map((g) => {
                      const of = rows.filter(({ r }) => reqGroup(r) === g);
                      return { g, total: of.length, met: of.filter(({ r }) => met(r)).length };
                    }).filter((c) => c.total > 0);
                    const gaps = rows.filter(({ r }) => !met(r)).length;
                    const shown = rows.filter(({ r }) =>
                      reqView === "all" ? true : reqView === "gaps" ? !met(r) : reqGroup(r) === reqView);
                    // overflow: CLIP, not hidden — `hidden` would make the box below the
                    // sticky containing block, so its header would scroll away with it
                    // instead of pinning to the drawer's scrollport (item 12). `clip`
                    // rounds the corners the same way without creating a scroller.
                    return (
                  <div ref={checklistRef} style={{ border: "1px solid var(--border-subtle)", borderRadius: 14, overflow: "clip", scrollMarginTop: 0 }}>
                    <div className="checklist-head">
                      <span style={{ fontSize: 13.5, fontWeight: 700, color: "var(--hue-teal-ink)" }}>Screening checklist</span>
                      <div className="mini-tabs" role="tablist" aria-label="Checklist view">
                        <button role="tab" aria-selected={reqView === "all"} className={reqView === "all" ? "is-on" : ""} onClick={() => setReqView("all")}>
                          All · {detail.req_summary}{coveredPct != null ? ` · ${coveredPct}%` : ""}
                        </button>
                        {gaps > 0 && (
                          <button role="tab" aria-selected={reqView === "gaps"} className={reqView === "gaps" ? "is-on" : ""} onClick={() => setReqView("gaps")}>
                            Gaps · {gaps}
                          </button>
                        )}
                        {counts.map((c) => (
                          <button key={c.g} role="tab" aria-selected={reqView === c.g} className={reqView === c.g ? "is-on" : ""} onClick={() => setReqView(c.g)}>
                            {REQ_VIEW_LABEL[c.g]} {c.met}/{c.total}
                          </button>
                        ))}
                      </div>
                    </div>
                    {shown.length === 0 && (
                      <div style={{ padding: "12px 14px", fontSize: 12.5, color: "var(--ink-soft)" }}>Nothing in this view.</div>
                    )}
                    {shown.map(({ r, i }) => (
                      <div key={i} id={`req-row-${i}`}
                           className={aimRow === i ? "req-aim" : undefined}
                           style={{ display: "flex", alignItems: "flex-start", gap: 12, padding: "10px 14px", borderBottom: "1px solid var(--neutral-150)", scrollMarginTop: 56 }}>
                        <span style={{ flex: "0 0 auto", width: 20, display: "inline-flex", justifyContent: "center" }}>
                          <Tip content={MATCH_MEANING[r.match] ?? r.match} maxWidth={230}>
                            <span aria-label={MATCH_MEANING[r.match] ?? r.match} title={MATCH_MEANING[r.match] ?? r.match}
                                  style={{ fontSize: 15, fontWeight: 700, cursor: "help", color: MATCH_COLOR[r.match] }}>{r.glyph}</span>
                          </Tip>
                        </span>
                        <div style={{ flex: "1 1 auto", minWidth: 0 }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                            <span style={{ fontSize: 13.5 }}>{r.label}</span>
                            {r.unverified && (
                              <span title="The AI judged this met but couldn't point at the exact words in the résumé, so the score leaves it out. Your call."
                                    style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.02em", color: "var(--hue-amber-ink)", background: "var(--hue-amber-tint)", border: "1px solid var(--hue-amber-solid)", borderRadius: 5, padding: "2px 6px" }}>
                                AI says met · unproven
                              </span>
                            )}
                            {r.must && <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--hue-green-ink)", background: "var(--hue-green-tint)", borderRadius: 5, padding: "2px 6px" }}>Must</span>}
                            {r.gate && <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--warn-ink)", background: "var(--surface-warn)", borderRadius: 5, padding: "2px 6px" }}>Gate</span>}
                          </div>
                          {r.evidence && (
                            <>
                              <button onClick={() => setOpenEvidence((o) => ({ ...o, [i]: !o[i] }))} style={{ background: "none", border: 0, cursor: "pointer", fontFamily: "inherit", fontSize: 12, fontWeight: 700, color: "var(--brand-green-deep)", padding: "4px 0 0" }}>
                                {openEvidence[i] ? "Hide evidence" : "Show evidence"}
                              </button>
                              {openEvidence[i] && (
                                <div style={{ fontSize: 12.5, color: "var(--ink-soft)", fontStyle: "italic", marginTop: 4, lineHeight: 1.45, borderLeft: "2px solid var(--border-subtle)", paddingLeft: 10 }}>
                                  “{r.evidence}”
                                </div>
                              )}
                            </>
                          )}
                          {(r.spans?.length ?? 0) > 0 && (
                            <ExperienceJourney spans={r.spans!} months={r.months ?? null}
                                               monthsTotal={r.months_total ?? null} />
                          )}
                          {!r.evidence && r.note && (
                            <div style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 3 }}>{r.note}</div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                    );
                  })()}
                  {/* A scroll runway under the filtered views (2026-09-07, item 4).
                      Switching from All to Gates shortens the page below what the pane
                      can scroll, so the browser clamps the scroll position and
                      everything lurches. With room left underneath, the checklist
                      header pins to the top and only the rows change. */}
                  {reqView !== "all" && <div aria-hidden="true" style={{ height: "42vh" }} />}
                </>
              )}

              {/* The résumé fills the drawer edge to edge and top to bottom (item 9): no
                  padding, no border, and the viewer is asked to fit the page WIDTH
                  (#view=FitH — Chromium/Edge honour it; others ignore it harmlessly). */}
              {section === "resume" && (
                blind ? (
                  <div style={{ padding: "30px 10px", textAlign: "center", fontSize: 13, color: "var(--ink-muted)" }}>
                    The original résumé is hidden during blind review.
                  </div>
                ) : (
                  <>
                    {/* Sticky HERE rather than in the stylesheet: this bar and the
                        scrolling pane above are one guarantee — the way out of a
                        preview that will not render must stay on screen — and a test
                        can hold us to it without a layout engine. */}
                    <div className="resume-bar" style={{ position: "sticky", top: 0, zIndex: 2 }}>
                      <span>{detail.resume_media_type === "pdf" ? "Original résumé" : "This résumé is a Word file"}</span>
                      {/* ?inline=1 or the server sends Content-Disposition: attachment
                          and the "open" link silently downloads instead (item 6). */}
                      <a href={detail.resume_media_type === "pdf" ? `${resumeHref}?inline=1` : resumeHref}
                         target="_blank" rel="noreferrer"
                         style={{ marginLeft: "auto", color: "var(--brand-green-deep)", fontWeight: 700, textDecoration: "none" }}>
                        {detail.resume_media_type === "pdf" ? "Open in a new tab ↗" : "Download ↓"}
                      </a>
                    </div>
                    {detail.resume_media_type === "pdf" ? (
                      <object
                        data={`${resumeHref}?inline=1#view=FitH&navpanes=0`}
                        type="application/pdf"
                        className="resume-frame"
                        aria-label="Résumé preview"
                      >
                        <div style={{ padding: "18px 16px", fontSize: 13, color: "var(--ink-soft)" }}>
                          The browser can't preview this PDF —{" "}
                          <a href={resumeHref} style={{ color: "var(--brand-green-deep)", fontWeight: 700 }}>download it instead</a>.
                        </div>
                      </object>
                    ) : (
                      <div style={{ padding: "30px 10px", textAlign: "center", fontSize: 13, color: "var(--ink-soft)" }}>
                        Word files don't preview here — <a href={resumeHref} style={{ color: "var(--brand-green-deep)", fontWeight: 700 }}>download it</a> to read it.
                      </div>
                    )}
                  </>
                )
              )}

              {section === "score" && verdictShown && (
                <>
                  {detail.score_math ? (
                    <div style={{ marginBottom: 14 }}>
                      <ScoreSummaryCard
                        math={detail.score_math}
                        stale={stale}
                        onJump={(sec) => openMathAt(`drawer-sm-${sec}`)}
                      />
                    </div>
                  ) : (
                    <div style={{ fontSize: 13, color: "var(--ink-soft)", marginBottom: 14 }}>{detail.sub}</div>
                  )}

                  {/* AI adjustments — what they are, said once (item 16) */}
                  {(detail.notes ?? []).length > 0 && (
                    <div ref={notesRef} style={{ scrollMarginTop: 12, marginBottom: 16 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
                        <SectionHead title="Score adjustments" count={(detail.notes ?? []).length} inline />
                        <InfoDot label="What the score adjustments are" content={ADJUSTMENTS_EXPLAINER} />
                      </div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        {(detail.notes ?? []).map((n, i) => (
                          <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", border: `1px solid ${n.flagged ? "var(--warn-border)" : "var(--border-subtle)"}`, borderRadius: 10, opacity: n.suspended ? 0.7 : 1 }}>
                            <span style={{ flex: "0 0 auto", minWidth: 26, height: 22, padding: "0 6px", borderRadius: 6, background: n.points >= 0 ? "var(--hue-green-tint)" : "var(--hue-amber-tint)", color: n.points >= 0 ? "var(--hue-green-ink)" : "var(--hue-amber-ink)", display: "inline-flex", alignItems: "center", justifyContent: "center", fontWeight: 700, fontSize: 12 }}>
                              {n.points >= 0 ? `+${n.points}` : n.points}
                            </span>
                            <span style={{ flex: "1 1 auto", fontSize: 13, minWidth: 0 }}>
                              {n.signal}
                              {n.suspended && <span style={{ textDecoration: "line-through", color: "var(--ink-muted)" }}> (set aside)</span>}
                              {n.evidence && <div style={{ fontSize: 11.5, color: "var(--ink-muted)", fontStyle: "italic", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={n.evidence}>“{n.evidence}”</div>}
                            </span>
                            {n.suspended && <span style={{ fontSize: 11, fontWeight: 700, color: "var(--warn-ink)", background: "var(--surface-warn)", border: "1px solid var(--warn-border)", borderRadius: 6, padding: "3px 8px", whiteSpace: "nowrap" }}>Set aside</span>}
                            {n.flagged && !n.suspended && <span title="Kept in the score — the fairness check flagged the reasoning for a human glance" style={{ fontSize: 11, fontWeight: 700, color: "var(--warn-ink)", background: "var(--surface-warn)", border: "1px solid var(--warn-border)", borderRadius: 6, padding: "3px 8px", whiteSpace: "nowrap" }}>Flagged</span>}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* preference notes (P40): the ±2 HR-preference nudges */}
                  {(detail.pref_notes ?? []).length > 0 && (
                    <div style={{ marginBottom: 16 }}>
                      <SectionHead title="Hiring preferences" count={(detail.pref_notes ?? []).length} />
                      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        {(detail.pref_notes ?? []).map((p, i) => (
                          <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", border: "1px solid var(--border-subtle)", borderRadius: 10 }}>
                            <span style={{ flex: "0 0 auto", minWidth: 26, height: 22, padding: "0 6px", borderRadius: 6, background: p.points >= 0 ? "var(--hue-violet-tint)" : "var(--hue-amber-tint)", color: p.points >= 0 ? "var(--hue-violet-ink)" : "var(--hue-amber-ink)", display: "inline-flex", alignItems: "center", justifyContent: "center", fontWeight: 700, fontSize: 12 }}>
                              {p.points >= 0 ? `+${p.points}` : p.points}
                            </span>
                            <span style={{ flex: "1 1 auto", fontSize: 13 }}>
                              {p.text}
                              <span style={{ color: "var(--ink-muted)" }}> · {p.owner_label}</span>
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* P51: the full score arithmetic, as-of the run's versions — folded */}
                  {detail.score_math && (
                    <div ref={mathRef} style={{ scrollMarginTop: 12, borderTop: "1px solid var(--neutral-150)", paddingTop: 12 }}>
                      <button
                        onClick={() => setShowMath((v) => !v)}
                        aria-expanded={showMath}
                        style={{ background: "none", border: 0, padding: 0, fontFamily: "inherit", fontSize: 12.5, fontWeight: 700, color: "var(--brand-green-deep)", cursor: "pointer" }}
                      >
                        {showMath ? "Hide the math" : `Show the math — how ${detail.score} was built`}
                      </button>
                      {showMath && <ScoreMathSection math={detail.score_math} idPrefix="drawer-sm" />}
                    </div>
                  )}
                </>
              )}

              {section === "notes" && (
                <>
                  {!takeMode && !readOnly && (
                    <SelfNoteSection reqId={reqId} candidateId={candidateId} initial={detail.self_note} />
                  )}
                  <div style={{ marginTop: takeMode || readOnly ? 0 : 18 }}>
                    <CommentsSection
                      reqId={reqId}
                      candidateId={candidateId}
                      comments={comments}
                      participants={participants}
                      flashId={flashCommentId}
                      hmView={takeMode}
                      readOnly={readOnly}
                      onAdded={(c) => { setComments((prev) => [...prev, c]); onCommented?.(candidateId); }}
                      onReact={react}
                    />
                  </div>
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/** The viewer's PRIVATE note (2026-07-20): only they see it, it saves on its own
 *  (no decision needed) and survives a re-screen. */
function SelfNoteSection({ reqId, candidateId, initial }: {
  reqId: string; candidateId: string; initial: { text: string; ts: string } | null | undefined;
}) {
  const [text, setText] = useState(initial?.text ?? "");
  const [saved, setSaved] = useState(initial?.text ?? "");
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState(false);
  useEffect(() => { setText(initial?.text ?? ""); setSaved(initial?.text ?? ""); }, [initial?.text, candidateId]);
  const dirty = text.trim() !== saved.trim();

  async function save() {
    if (busy || !dirty) return;
    setBusy(true);
    try {
      const r = await put<{ text: string }>(`/api/roles/${reqId}/candidates/${candidateId}/note`, { text });
      setSaved(r.text); setText(r.text);
      setFlash(true); window.setTimeout(() => setFlash(false), 1600);
    } catch { /* keep the draft so nothing is lost */ } finally { setBusy(false); }
  }

  return (
    <div>
      <SectionHead title="Note to self · only you can see this" />
      <textarea
        className="input" rows={2}
        style={{ width: "100%", resize: "vertical", minHeight: 40 }}
        aria-label="Note to self"
        placeholder="Private to you — Enter saves, Shift+Enter for a new line…"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void save(); } }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 6 }}>
        <button
          onClick={() => void save()}
          disabled={busy || !dirty}
          style={{ height: 30, padding: "0 12px", borderRadius: 8, border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink-soft)", fontFamily: "inherit", fontSize: 12, fontWeight: 700, cursor: busy || !dirty ? "default" : "pointer", opacity: busy || !dirty ? 0.55 : 1 }}
        >
          {busy ? "Saving…" : "Save note"}
        </button>
        {flash && <span style={{ fontSize: 11.5, color: "var(--brand-green-deep)" }}>Saved ✓</span>}
      </div>
    </div>
  );
}

// Bold @-mention tokens in a rendered comment so the person named stands out.
const MENTION_RE = /(@[\p{L}][\p{L}]*(?: [\p{L}][\p{L}]*)?)/gu;
function renderWithMentions(text: string): React.ReactNode {
  return text.split(MENTION_RE).map((part, i) =>
    part.startsWith("@") && i % 2 === 1
      ? <span key={i} style={{ fontWeight: 700, color: "var(--brand-green-deep)" }}>{part}</span>
      : <span key={i}>{part}</span>);
}

/** Emoji reactions on one comment (2026-07-20): existing pills (highlighted when
 *  mine, hover shows who) + a small palette to add one. */
function ReactionBar({ reactions, readOnly = false, onReact }: { reactions: ReactionView[]; readOnly?: boolean; onReact: (emoji: string) => void }) {
  const [open, setOpen] = useState(false);
  if (readOnly && reactions.length === 0) return null; // nothing to show, nothing to add
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 7, flexWrap: "wrap", position: "relative" }}>
      {reactions.map((r) => (
        <button
          key={r.emoji}
          onClick={() => !readOnly && onReact(r.emoji)}
          title={r.who.join(", ")}
          style={{ display: "inline-flex", alignItems: "center", gap: 4, height: 22, padding: "0 7px", borderRadius: 999, cursor: readOnly ? "default" : "pointer", fontFamily: "inherit", fontSize: 11.5, fontWeight: 700, background: r.mine ? "var(--brand-green-tint)" : "var(--neutral-150)", border: `1px solid ${r.mine ? "var(--brand-green)" : "var(--border-subtle)"}`, color: "var(--ink-soft)" }}
        >
          <span aria-hidden="true">{r.emoji}</span>
          <span>{r.count}</span>
        </button>
      ))}
      {!readOnly && (
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label="Add reaction"
        style={{ width: 24, height: 22, borderRadius: 999, border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink-muted)", cursor: "pointer", fontSize: 12, lineHeight: 1 }}
      >
        ☺
      </button>
      )}
      {open && (
        <div style={{ position: "absolute", top: 26, left: 0, zIndex: 5, display: "flex", gap: 3, padding: "5px 7px", background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 10, boxShadow: "var(--shadow-md)" }}>
          {REACTIONS.map((e) => (
            <button key={e} onClick={() => { onReact(e); setOpen(false); }}
              style={{ width: 26, height: 26, border: 0, background: "none", cursor: "pointer", fontSize: 15, borderRadius: 6 }}>
              {e}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** A mention candidate: someone on this requisition, or anyone the directory knows. */
interface MentionOption { email: string; name: string; role: string; outside?: boolean }

/** The candidate's comment thread + composer (2026-07-20): live both ways,
 *  emoji reactions, and @-mentions. The roster (2026-09-06) is the requisition's
 *  people first, then the WHOLE people directory as you type — anyone at Acme
 *  the directory knows. Someone who does not use the app yet is emailed the comment
 *  with a link back here (server-side), which is how the tool reaches new people. */
function CommentsSection({ reqId, candidateId, comments, participants, flashId, hmView, readOnly = false, onAdded, onReact }: {
  reqId: string;
  candidateId: string;
  comments: CandidateComment[];
  participants: Participant[];
  flashId: string | null;
  hmView: boolean;
  readOnly?: boolean;
  onAdded: (c: CandidateComment) => void;
  onReact: (commentId: string, emoji: string) => void;
}) {
  const me = useMe();
  const people = usePeople();
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [mentioned, setMentioned] = useState<Record<string, string>>({}); // "@Name" -> email
  const [pick, setPick] = useState<{ q: string; at: number } | null>(null); // active @-token
  const [pickActive, setPickActive] = useState(0); // highlighted mention suggestion
  const [remote, setRemote] = useState<MentionOption[]>([]); // directory hits for the token
  const taRef = useRef<HTMLTextAreaElement>(null);
  const seq = useRef(0);
  const myEmail = (me?.user ?? "").toLowerCase();

  // Directory search for the @-token (debounced): widens the roster beyond the
  // requisition's own people. Failures just leave the local roster.
  useEffect(() => {
    const q = pick?.q.trim() ?? "";
    if (q.length < 2) { setRemote([]); return; }
    const mine = ++seq.current;
    const t = window.setTimeout(() => {
      get<PeopleSearch>(`/api/people?q=${encodeURIComponent(q)}`)
        .then((r) => {
          if (seq.current !== mine) return;
          const known = new Set(participants.map((p) => p.email.toLowerCase()));
          setRemote((r.people ?? [])
            .filter((p) => !known.has(p.email.toLowerCase()) && p.email.toLowerCase() !== myEmail)
            .map((p) => ({ email: p.email, name: p.name || p.email, role: p.job_title || "Directory", outside: true })));
        })
        .catch(() => { if (seq.current === mine) setRemote([]); });
    }, 180);
    return () => window.clearTimeout(t);
  }, [pick?.q, participants, myEmail]);

  const suggestions: MentionOption[] = pick
    ? [
        ...participants.filter((p) => p.name.toLowerCase().includes(pick.q.toLowerCase())).slice(0, 5),
        ...remote,
      ].slice(0, 8)
    : [];

  function onDraftChange(v: string, caret: number) {
    setDraft(v);
    const m = /(?:^|\s)@([\p{L}]*)$/u.exec(v.slice(0, caret)); // an @-token up to the caret
    setPick(m ? { q: m[1], at: caret - m[1].length - 1 } : null);
    setPickActive(0);
  }

  // Keyboard: with the @-mention list open, ↑/↓ move and Enter/Tab select; Esc
  // closes it. Otherwise Enter (no Shift) sends the comment (2026-07-21).
  function onComposerKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (pick && suggestions.length) {
      if (e.key === "ArrowDown") { e.preventDefault(); setPickActive((i) => (i + 1) % suggestions.length); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setPickActive((i) => (i - 1 + suggestions.length) % suggestions.length); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); insertMention(suggestions[Math.min(pickActive, suggestions.length - 1)]); return; }
      if (e.key === "Escape") { e.preventDefault(); setPick(null); return; }
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); }
  }

  function insertMention(p: MentionOption) {
    if (!pick) return;
    const token = `@${p.name}`;
    const next = `${draft.slice(0, pick.at)}${token} ${draft.slice(pick.at + 1 + pick.q.length)}`;
    setDraft(next);
    setMentioned((mm) => ({ ...mm, [token]: p.email }));
    setPick(null);
    window.setTimeout(() => taRef.current?.focus(), 0);
  }

  function draftMentions(text: string): string[] {
    const out = new Set<string>();
    for (const [token, email] of Object.entries(mentioned)) if (text.includes(token)) out.add(email);
    return Array.from(out);
  }

  async function send() {
    const text = draft.trim();
    if (!text || busy) return;
    setBusy(true); setErr(null);
    try {
      const res = await post<{ ok: boolean; comment: CandidateComment }>(
        `/api/roles/${reqId}/candidates/${candidateId}/comments`,
        { text, mentions: draftMentions(text) });
      onAdded(res.comment);
      setDraft(""); setMentioned({}); setPick(null);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const outsideMentioned = Object.values(mentioned).some((email) =>
    draft.includes(Object.keys(mentioned).find((k) => mentioned[k] === email) ?? "")
    && !participants.some((p) => p.email.toLowerCase() === email.toLowerCase()));

  return (
    <div>
      <SectionHead title="Comments" count={comments.length} />
      {comments.length === 0 && (
        <div style={{ fontSize: 12.5, color: "var(--ink-muted)", marginBottom: 10 }}>
          No comments yet. Anything written here reaches the recruiting team and the hiring manager right away; @-mention anyone at Acme to bring them in.
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 10 }}>
        {comments.map((c) => {
          const mine = c.author.toLowerCase() === myEmail;
          const who = mine ? "You" : personName(c.author, people, c.author_name);
          return (
            <div
              key={c.comment_id}
              id={`comment-${c.comment_id}`}
              style={{
                border: "1px solid var(--border-subtle)", borderRadius: 10, padding: "9px 12px",
                background: mine ? "var(--brand-green-tint)" : "var(--white)",
                animation: flashId === c.comment_id ? "brss-flash 1.4s cubic-bezier(0.2,0,0,1) 2" : undefined,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 3 }}>
                <Avatar email={c.author} name={who === "You" ? personName(c.author, people, c.author_name) : who} size="xs" photo={people.get(c.author.toLowerCase())?.photo} hue={people.get(c.author.toLowerCase())?.hue} />
                <span style={{ fontSize: 12, fontWeight: 700 }} title={c.author}>{who}</span>
                {c.author_role && !mine && (
                  <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: c.author_role === "Hiring manager" ? "var(--hue-violet-ink)" : "var(--ink-soft)", background: c.author_role === "Hiring manager" ? "var(--hue-violet-tint)" : "var(--neutral-150)", borderRadius: 5, padding: "2px 6px" }}>
                    {c.author_role}
                  </span>
                )}
                <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--ink-muted)" }}>
                  {c.ts ? new Date(c.ts).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : ""}
                </span>
              </div>
              <div style={{ fontSize: 13, lineHeight: 1.5, whiteSpace: "pre-wrap" }}>{renderWithMentions(c.text)}</div>
              <ReactionBar reactions={c.reactions ?? []} readOnly={readOnly} onReact={(e) => onReact(c.comment_id, e)} />
            </div>
          );
        })}
      </div>
      {readOnly ? (
        <div style={{ fontSize: 12, color: "var(--ink-muted)" }}>Read-only view — you can read the thread but not comment.</div>
      ) : (
      <>
      <div style={{ display: "flex", gap: 8, alignItems: "flex-start", position: "relative" }}>
        <textarea
          ref={taRef}
          className="input"
          rows={2}
          style={{ flex: "1 1 auto", resize: "vertical", minHeight: 40 }}
          placeholder={hmView ? "Comment for the recruiter — type @ to mention someone…" : "Comment — the team sees it right away. Type @ to mention anyone…"}
          value={draft}
          onChange={(e) => onDraftChange(e.target.value, e.target.selectionStart ?? e.target.value.length)}
          onKeyDown={onComposerKey}
        />
        {pick && suggestions.length > 0 && (
          <div style={{ position: "absolute", left: 0, bottom: "100%", marginBottom: 6, zIndex: 6, minWidth: 260, background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 10, boxShadow: "var(--shadow-md)", overflow: "hidden" }}>
            {suggestions.map((p, i) => (
              <button
                key={p.email}
                onMouseDown={(e) => { e.preventDefault(); insertMention(p); }}
                onMouseEnter={() => setPickActive(i)}
                style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", textAlign: "left", padding: "8px 12px", border: 0, borderBottom: "1px solid var(--neutral-150)", background: i === pickActive ? "var(--brand-green-tint)" : "var(--white)", cursor: "pointer", fontFamily: "inherit" }}
              >
                <Avatar email={p.email} name={p.name} size="xs" />
                <span style={{ fontSize: 12.5, fontWeight: 700, color: "var(--ink)", flex: "1 1 auto" }}>{p.name}</span>
                <span style={{ fontSize: 10.5, color: "var(--ink-muted)" }}>{p.outside ? `${p.role} · will be emailed` : p.role}</span>
              </button>
            ))}
          </div>
        )}
        <button
          onClick={() => void send()}
          disabled={busy || !draft.trim()}
          style={{ flex: "0 0 auto", height: 38, padding: "0 14px", borderRadius: 10, border: "1px solid var(--brand-green)", background: "var(--brand-green-tint)", color: "var(--brand-green-deep)", fontFamily: "inherit", fontSize: 12.5, fontWeight: 700, cursor: busy || !draft.trim() ? "default" : "pointer", opacity: busy || !draft.trim() ? 0.55 : 1, display: "inline-flex", alignItems: "center", gap: 6 }}
        >
          <IconComment size={13} /> Comment
        </button>
      </div>
      {outsideMentioned && (
        <div style={{ fontSize: 11.5, color: "var(--ink-soft)", marginTop: 6 }}>
          Someone mentioned here doesn't use Resume Screener yet — they'll get this comment by email with a link to the candidate.
        </div>
      )}
      </>
      )}
      {err && <div style={{ fontSize: 12, color: "var(--warn-ink)", marginTop: 6 }}>{err}</div>}
    </div>
  );
}

// 13px ink, not 11px uppercase ink-soft (2026-09-07): "Strengths" and "Watch-outs"
// were the two labels people said they never noticed.
function SectionHead({ title, count, inline = false }: { title: string; count?: number; inline?: boolean }) {
  return (
    <div style={{ fontSize: 13, fontWeight: 800, letterSpacing: "-0.005em", color: "var(--ink)", marginBottom: inline ? 0 : 8 }}>
      {title}
      {count != null && <span style={{ marginLeft: 6, fontWeight: 700, color: "var(--ink-muted)" }}>({count})</span>}
    </div>
  );
}

/** "32" -> "2 yr 8 mo". The drawer says the total in the same words the note does. */
export function monthsLabel(months: number): string {
  const y = Math.floor(months / 12), m = months % 12;
  if (y && m) return `${y} yr ${m} mo`;
  return y ? `${y} yr` : `${m} mo`;
}

/** How the score was built, in the parts a recruiter can act on (2026-09-11, pilot
 *  ask 5). It read "Covers 96% of weighted requirements (base 96). 2 evidenced signal
 *  adjustment(s), net +6.0." — engine vocabulary, and nothing said which half came
 *  from the checklist they wrote themselves. Now the checklist number opens the
 *  checklist, the AI number opens the adjustments that produced it, and the sum
 *  between them is on screen.
 *
 *  Built from the score TRACE, never by re-reading the stored sentence: the record
 *  keeps `summary_rationale` exactly as the engine wrote it, and a run too old to
 *  carry a trace still shows that sentence rather than nothing. */
export function ScoreLine({ math, verdict, onChecklist, onAdjustments }: {
  math: ScoreMath | null;
  verdict: string;
  onChecklist: () => void;
  onAdjustments: () => void;
}) {
  if (!math) return <p style={{ fontSize: 14, lineHeight: 1.55, margin: 0 }}>{verdict}</p>;
  const signed = (n: number) => `${n < 0 ? "−" : "+"}${Math.abs(Math.round(n * 10) / 10)}`;
  const ai = math.adjustments.net;
  const prefs = math.preferences.net;
  const setAside = math.adjustments.suspended.length;
  const note = math.band.gate_note ?? math.band.cap_note ?? null;
  const capped = math.total.clamped === "high" ? math.band.clamp_max ?? 100
    : math.total.clamped === "low" ? math.band.clamp_min ?? 0 : null;
  return (
    <>
      <div className="score-line">
        <button className="score-part" onClick={onChecklist} title="Show the screening checklist">
          <strong>{Math.round(math.base.total)}%</strong> of the checklist
        </button>
        {/* The sign rides ON the number, not on a separator beside it: "+6 by the AI"
            is one fact a screen reader can also read out, and it is the phrasing the
            pilot used when they asked for this. */}
        {(ai !== 0 || math.adjustments.lines.length > 0) && (
          <button className="score-part" onClick={onAdjustments} title="Show what the AI added or deducted">
            <strong>{signed(ai)}</strong> by the AI
          </button>
        )}
        {prefs !== 0 && (
          <button className="score-part" onClick={onAdjustments} title="Show the hiring preferences applied">
            <strong>{signed(prefs)}</strong> from preferences
          </button>
        )}
        <span className="score-op" aria-hidden="true">=</span>
        <span className="score-final">{Math.round(math.total.final)}<span>/100</span></span>
        {capped != null && <span className="score-cap">capped at {capped}</span>}
        {setAside > 0 && (
          <button className="score-aside" onClick={onAdjustments}
                  title="The fairness check set these aside — they are shown but don't count">
            {setAside} set aside
          </button>
        )}
      </div>
      {note && <p className="score-line-note">{note}</p>}
    </>
  );
}

const MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2019-03" → "Mar 2019", "2019" → "2019", an open span → "Present". Formatting
 *  only: whether a span is open and how long it ran were decided server-side. */
export function periodLabel(sp: ReqSpan): string {
  const one = (raw: string) => {
    const t = (raw || "").trim();
    const m = /^(\d{4})[-/](\d{1,2})$/.exec(t);
    if (!m) return t || "?";
    const mi = Number(m[2]);
    return mi >= 1 && mi <= 12 ? `${MONTH_ABBR[mi - 1]} ${m[1]}` : t;
  };
  return `${one(sp.start)} – ${sp.ongoing ? "Present" : one(sp.end)}`;
}

/** The dated positions behind a length-of-experience requirement, drawn as a journey
 *  (2026-09-11 pilot ask 3). The lines were always shown, stacked as quotes, and a
 *  reviewer had to do the reading: which job, when, how long. Now each one is a step
 *  — when · how long · the line it was read off — with the bar sized against the
 *  longest, so a five-year stint and a six-month one differ before a number is read.
 *
 *  Nothing here parses a date or adds anything up: `months` per row and the ordering
 *  come from review._span_view, and the total is the engine's merged figure, so the
 *  picture and the arithmetic cannot drift apart.
 *
 *  The total is what THIS requirement counted (2026-09-25). "Minimum 7 years managing AP
 *  regional projects" counted two of a ten-year résumé's five jobs; under the old
 *  heading "Counted from the résumé" the 2 yr 1 mo read as the candidate's whole career,
 *  and the other three jobs were not on screen to disagree with. Now every dated job is a
 *  row, the ones left out are muted with the AI's reason, and the all-jobs total sits
 *  beside the counted one. */
export function ExperienceJourney({ spans, months, monthsTotal = null }: {
  spans: ReqSpan[]; months: number | null; monthsTotal?: number | null;
}) {
  const longest = Math.max(1, ...spans.map((s) => s.months ?? 0));
  const counted = spans.filter((s) => s.counted !== false);
  const leftOut = spans.length - counted.length;
  const summed = counted.reduce((n, s) => n + (s.months ?? 0), 0);
  // Two jobs held at once are one stretch of a working life, so the rows can add up
  // to more than the total beside them. Said in one line — otherwise it reads as a
  // mistake in a number a gate decision may rest on.
  const overlapped = months != null && summed > months;
  // The journey is open by default — it is the working, and showing it for everyone was
  // the whole ask. But it is long, and once a reviewer has checked the addition they
  // want the row back: the headline folds it away and keeps the answer (2026-09-11).
  const [open, setOpen] = useState(true);
  return (
    <div className="exp-journey">
      <button type="button" className="exp-head" aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
              title={open ? "Hide the roles this was counted from" : "Show the roles this was counted from"}>
        {open ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}
        <span className="exp-title">Counted toward this requirement</span>
        {months != null && <span className="exp-total">{monthsLabel(months)}</span>}
        <span className="exp-sub">
          {leftOut > 0 ? `${counted.length} of ${spans.length} roles`
                       : `${spans.length} ${spans.length === 1 ? "role" : "roles"}`}
        </span>
      </button>
      {/* Beside the headline, not in the fold: it is part of the answer — without it a
          scoped total reads as the whole career. */}
      {leftOut > 0 && monthsTotal != null && (
        <div className="exp-all">All dated roles on the résumé: {monthsLabel(monthsTotal)}</div>
      )}
      {open && (
        <ol className="exp-rows">
          {/* Newest first — the server orders them (review._span_view), because the
              dates are read in exactly one place. Left-out jobs keep their place in
              the timeline, muted, so the gap in the count is visible where it is. */}
          {spans.map((sp, k) => {
            const out = sp.counted === false;
            const cls = [sp.title ? "" : "no-role", out ? "not-counted" : ""].filter(Boolean).join(" ");
            return (
              <li key={k} className={cls || undefined}>
                <span className="exp-when">{periodLabel(sp)}</span>
                {sp.title && <span className="exp-role">{sp.title}</span>}
                <span className="exp-bar" aria-hidden="true">
                  <span style={{ width: `${Math.round(((sp.months ?? 0) / longest) * 100)}%` }} />
                </span>
                <span className="exp-len">{sp.months != null ? monthsLabel(sp.months) : "—"}</span>
                {out && (
                  <span className="exp-why">
                    Not counted{sp.why_not ? ` — ${sp.why_not}` : ""}
                  </span>
                )}
                <span className="exp-quote">“{sp.quote}”</span>
              </li>
            );
          })}
        </ol>
      )}
      {open && overlapped && <div className="exp-note">Roles held at the same time are counted once.</div>}
    </div>
  );
}

/** A strength / watch-out line. With `onClick` it becomes a button that opens the
 *  requirement it names in the checklist below (2026-09-07, item 3) — the chips used
 *  to state a gap with no way to see what the résumé actually said. */
function Line({ bg, icon, children, onClick }: {
  bg: string; icon: React.ReactNode; children: React.ReactNode; onClick?: () => void;
}) {
  const inner = (
    <>
      <span style={{ flex: "0 0 auto", marginTop: 1, display: "inline-flex", width: 15, justifyContent: "center" }}>{icon}</span>
      <span style={{ textAlign: "left" }}>{children}</span>
    </>
  );
  const style: React.CSSProperties = {
    // 12px, not 12.5 (2026-09-07): with the section headings at 13px these read as
    // nearly the same size, so the list had no hierarchy at a glance.
    display: "inline-flex", alignItems: "flex-start", gap: 8, fontSize: 12,
    lineHeight: 1.45, color: "var(--ink)", background: bg, borderRadius: 8,
    padding: "6px 10px",
  };
  if (!onClick) return <span style={style}>{inner}</span>;
  return (
    <button type="button" onClick={onClick} className="ev-line"
            title="Show this requirement and its evidence"
            style={{ ...style, border: 0, fontFamily: "inherit", cursor: "pointer", width: "100%" }}>
      {inner}
    </button>
  );
}
