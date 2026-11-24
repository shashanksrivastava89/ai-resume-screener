// The workspace's Checklist tab. ONE column (2026-09-07): a collapsed job-description
// band, then the requirement cards + the "Check & add" row, then the publish bar at
// the end of the form. The old 320px right rail (activity + JD) is gone — it cost the
// editor a third of its width permanently. For a recruiter it's the
// editor (edit → publish the next version; flagged rows route through HR-Head
// approval in ONE click). For the assigned hiring manager it flips to SUGGEST
// MODE: read-only rows, each offering "Suggest edit" (INPUT — the TA reviews
// and publishes). HM collaboration is dark-launched via capabilities (P42).
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Page } from "../../layout/AppShell";
import { Button, MagicButton, Callout, CenterLoading, PageError } from "../../components/ui";
import ChecklistEditor, { KindLegend, type CheckVerdict } from "../../components/ChecklistEditor";
import ActivityRail from "../../components/ActivityRail";
import LockControl from "../../components/LockControl";
import JdPane from "../../components/JdPane";
import ChecklistVersions from "../../components/ChecklistVersions";
import { IconClock } from "../../components/icons";
import SuggestionCard from "../../components/SuggestionCard";
import SuggestComposer, { type SuggestPayload } from "../../components/SuggestComposer";
import { ConfirmModal, useToast } from "../../components/overlays";
import { get, post, put } from "../../api";
import { useWorkspace, tabPath } from "./context";
import { useMe } from "../../MeContext";
import { useFetch, useFeatureFlag, invalidate } from "../../hooks";
import { isHmActive } from "../../hm";
import { useLeaveGuard } from "../../leave";
import { Person, usePeople } from "../../people";
import {
  loadChecklistDraft, saveChecklistDraft, clearChecklistDraft, rowsEqual,
  type ChecklistDraft,
} from "../../drafts";
import type { ChecklistApproval, JdPayload, Requirement, Rubric, RoleDetail, Suggestion } from "../../types";
import { dualStamp } from "../../time";

/** Notification deep link (?focus=sugg:<id>): resolve the suggestion id once
 *  the list is loaded, hand back the id to flash, scroll its anchor into view
 *  and clear the param. `anchorOf` maps the id to the DOM id to scroll to. */
function useSuggestionFocus(ready: boolean, anchorOf: (sid: string) => string | null): string | null {
  const [searchParams, setSearchParams] = useSearchParams();
  const [flash, setFlash] = useState<string | null>(null);
  useEffect(() => {
    const focus = searchParams.get("focus");
    if (!ready || !focus?.startsWith("sugg:")) return;
    const sid = focus.slice("sugg:".length);
    const anchor = anchorOf(sid);
    if (anchor) {
      setFlash(sid);
      window.setTimeout(() => {
        document.getElementById(anchor)?.scrollIntoView?.({ block: "center", behavior: "smooth" });
      }, 150);
      window.setTimeout(() => setFlash(null), 3200);
    }
    searchParams.delete("focus");
    setSearchParams(searchParams, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, searchParams]);
  return flash;
}

const SUGG_FLASH = "brss-flash 1.4s cubic-bezier(0.2,0,0,1) 2";

// The caption over the requirement cards: how many, which version is in force, and
// the colour key. 2026-09-07: the three used to be one long line that ran into the
// legend on a narrow column — they are now a wrapping row, and the version is a
// BUTTON (the history is the most consequential thing on this page: every score on
// the Review tab was produced against some version of this list).
function CaptionRow({ count, version, dirty, onOpenHistory, right }: {
  count: number; version: number | null; dirty?: boolean;
  onOpenHistory?: () => void; right?: React.ReactNode;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", rowGap: 6 }}>
      <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--ink-soft)" }}>
        {count} requirement{count === 1 ? "" : "s"}
      </span>
      {version ? (
        <button onClick={onOpenHistory} className="ver-link"
                title="Every published version — who published it, and whether anyone was screened against it">
          <IconClock size={12} />
          {dirty ? <>draft v{version + 1} · <strong>v{version}</strong> in force</> : <><strong>v{version}</strong> in force</>}
        </button>
      ) : (
        <span style={{ fontSize: 11.5, color: "var(--ink-muted)" }}>nothing published yet</span>
      )}
      <span style={{ marginLeft: "auto" }}>{right}</span>
    </div>
  );
}

// ---- recruiter (TA) editor + collaboration ----------------------------------------

export function ChecklistPanel({ role, reload, hmPortalOn = false }: { role: RoleDetail; reload: () => void; hmPortalOn?: boolean }) {
  const reqId = role.req_id;
  const nav = useNavigate();
  const location = useLocation();
  const toast = useToast();
  // Overview's "update JD → reset checklist" flow lands here with this flag so we
  // auto-draft a fresh checklist from the new JD for the recruiter to re-approve.
  const redraftRequested = (location.state as { redraft?: boolean } | null)?.redraft ?? false;
  const redraftedRef = useRef(false);
  const [reqs, setReqs] = useState<Requirement[] | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [sBusy, setSBusy] = useState<string | null>(null);
  const [approval, setApproval] = useState<ChecklistApproval | null>(null); // P43 sign-off state
  const [resetSignal, setResetSignal] = useState(0); // bump to clear the editor's undo stack
  const [serverRows, setServerRows] = useState<Requirement[]>([]); // last published rows (draft baseline)
  const [pendingDraft, setPendingDraft] = useState<ChecklistDraft | null>(null); // restore prompt
  const [historyOpen, setHistoryOpen] = useState(false); // the version-history dialog
  const [discardAsk, setDiscardAsk] = useState(false);   // "Discard edits" confirm
  const me = useMe();
  const { readOnly, closed } = useWorkspace();
  const canEditRules = me?.can_edit_rules ?? false;
  const hmName = role.hm?.name || role.hm?.email || "the hiring manager";
  // The JD reference pane (pilot feedback, P49 UX addendum): TAs read the JD to
  // decide must-have vs hard-gate rather than trusting the model's parse.
  const { data: jd } = useFetch<JdPayload>(`/api/roles/${reqId}/jd`);
  // The manual lock is hidden behind a flag (2026-09-06, pilot feedback item 13: it
  // was hard to understand). An EXISTING lock still shows and still binds — hiding
  // the button must never hide the state.
  const locksOn = useFeatureFlag("manual_locks") ?? false;
  // A REAL change, not a focused textbox: the publish button used to light up on
  // any render, so people published no-op versions or believed they had published.
  // Dirty = the rows differ from what is on file (ids/critic metadata ignored).
  const dirty = reqs !== null && !rowsEqual(reqs, serverRows);
  useLeaveGuard(dirty ? "This checklist has edits that are not published yet." : null);

  const loadRubric = () =>
    get<Rubric>(`/api/roles/${reqId}/rubric`)
      .then((rubric) => {
        setVersion(rubric.version);
        setApproval(rubric.approval ?? null);
        const rows = rubric.requirements.length
          ? rubric.requirements
          : [{ text: "", kind: "must_have" as const, importance: 4, type: "other" }];
        setServerRows(rows);
        setReqs(rows);
        // An unsaved local draft that differs from what's published → offer to
        // restore it (never auto-apply). Skipped when it matches the server.
        // A draft from BEFORE the current published version is OBSOLETE — the
        // checklist was published again since (e.g. after accepting an HM
        // suggestion), so drop it silently instead of the "random" restore prompt
        // (2026-07-21). Only genuine unsaved edits on the current version prompt.
        const draft = loadChecklistDraft(reqId);
        if (draft && draft.baseVersion !== rubric.version) {
          clearChecklistDraft(reqId);
          setPendingDraft(null);
        } else {
          setPendingDraft(draft && !rowsEqual(draft.rows, rows) ? draft : null);
        }
      });

  useEffect(() => {
    loadRubric().catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reqId]);

  // Auto re-draft once when arriving from an Overview JD change — then clear the
  // router flag so a refresh doesn't re-trigger it.
  useEffect(() => {
    if (!redraftRequested || redraftedRef.current || reqs === null) return;
    redraftedRef.current = true;
    nav(".", { replace: true, state: {} });
    void draftFromJd();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [redraftRequested, reqs]);

  // Autosave edits to a local draft (debounced) — so a refresh or accidental
  // nav never loses a long editing session. Skip while a restore prompt is
  // pending (don't clobber the stored draft with the untouched server rows) and
  // when the rows still match what's published (no no-op drafts).
  useEffect(() => {
    if (!reqs || pendingDraft) return;
    if (rowsEqual(reqs, serverRows)) return;
    const id = window.setTimeout(() => {
      saveChecklistDraft(reqId, { savedAt: new Date().toISOString(), baseVersion: version, rows: reqs });
    }, 600);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reqs, pendingDraft, serverRows, version, reqId]);

  function restoreDraft() {
    if (!pendingDraft) return;
    setReqs(pendingDraft.rows);
    setResetSignal((s) => s + 1);
    setPendingDraft(null);
  }
  function discardDraft() {
    clearChecklistDraft(reqId);
    setPendingDraft(null);
  }

  // Draft the checklist from the JD for an existing empty req (the same AI draft
  // the wizard runs) — fills the editor for the recruiter to edit; publishing
  // stays an explicit, separate action.
  async function draftFromJd() {
    setBusy(true);
    setError(null);
    try {
      const rubric = await post<Rubric>(`/api/roles/${reqId}/rubric/draft`, {});
      setReqs(rubric.requirements);
      setResetSignal((s) => s + 1);
      toast("Draft ready — edit and publish to make it count");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const loadSuggestions = () =>
    get<{ suggestions: Suggestion[] }>(`/api/roles/${reqId}/suggestions`)
      .then((d) => setSuggestions(d.suggestions))
      .catch(() => setSuggestions([]));

  useEffect(() => {
    if (hmPortalOn) loadSuggestions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reqId, hmPortalOn]);

  // The design's ONE publish action: a clean draft publishes; a flagged draft is
  // sent for HR-Head approval in the same click (publish + request collapsed).
  // A clean publish then moves on to Candidates — the next step — so the
  // recruiter is not left on a page whose work is done (item 10).
  async function publish() {
    if (!reqs) return;
    setBusy(true);
    setError(null);
    try {
      const res = await put<Rubric>(`/api/roles/${reqId}/rubric`, {
        requirements: reqs.filter((r) => r.text.trim()),
      });
      clearChecklistDraft(reqId); // the edits are now published — draft is spent
      invalidate(`/api/roles/${reqId}`); // rubric/gate/stage changed
      await loadRubric().catch(() => {});
      reload();
      if (res.needs_approval) {
        await post(`/api/roles/${reqId}/checklist-approval/request`, {}).catch(() => {});
        toast("Sent to the HR Head — publishes on approval");
      } else {
        toast(`v${res.version ?? (version ?? 0) + 1} published — next: add résumés`);
        nav(tabPath(reqId, "candidates"));
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // The already-published-but-unsent state (a failed auto-request, or a
  // pre-existing flagged version): the ONE button just sends the request.
  async function requestApproval() {
    setBusy(true);
    setError(null);
    try {
      await post(`/api/roles/${reqId}/checklist-approval/request`, {});
      await loadRubric();
      toast(`Sent v${approval?.version ?? version} to the HR Head for approval`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function approveChecklist() {
    setBusy(true);
    setError(null);
    try {
      await post(`/api/roles/${reqId}/checklist-approval/approve`, {});
      await loadRubric();
      reload();
      toast(`Approved v${approval?.version ?? version} — this checklist can now be used`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // Accepting a suggestion returns merged_text (+ kind/importance when the HM
  // proposed those too); we splice them into the matching requirement's draft
  // so the TA can review and then publish. Accepting alone writes nothing.
  async function acceptSuggestion(s: Suggestion, editedText?: string) {
    setSBusy(s.suggestion_id);
    try {
      const res = await post<{ requirement_id: string; merged_text: string;
                               merged_kind?: string | null; merged_importance?: number | null;
                               new_row?: boolean }>(
        `/api/suggestions/${s.suggestion_id}/accept`,
        editedText ? { edited_text: editedText } : {}
      );
      setReqs((prev) => {
        const list = prev ?? [];
        // A new-row suggestion (no requirement_id) is APPENDED, not spliced —
        // publish then renumbers ids and routes any flag through HR-Head approval.
        if (res.new_row || !res.requirement_id) {
          return [...list, {
            text: res.merged_text, type: "other",
            kind: (res.merged_kind as Requirement["kind"]) ?? "nice_to_have",
            importance: res.merged_importance ?? 3,
          }];
        }
        return list.map((r) => (r.id && r.id === res.requirement_id
          ? { ...r, text: res.merged_text,
              ...(res.merged_kind ? { kind: res.merged_kind as Requirement["kind"] } : {}),
              ...(res.merged_importance != null ? { importance: res.merged_importance } : {}) }
          : r));
      });
      toast(res.new_row ? "New requirement added — publish to make it count" : "Suggestion merged — publish to make it count");
      await loadSuggestions();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSBusy(null);
    }
  }

  // Accept every open suggestion in one pass (2026-07-19) — sequential so each
  // merge lands before the next; one failure stops with a visible error.
  async function acceptAll(open: Suggestion[]) {
    for (const s of open) {
      // eslint-disable-next-line no-await-in-loop
      await acceptSuggestion(s);
    }
  }

  async function declineSuggestion(s: Suggestion, note: string) {
    setSBusy(s.suggestion_id);
    try {
      await post(`/api/suggestions/${s.suggestion_id}/decline`, note ? { note } : {});
      toast(`Declined — ${hmName} is notified${note ? " with your note" : ""}`);
      await loadSuggestions();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSBusy(null);
    }
  }

  async function requestReview() {
    setError(null);
    try {
      await post(`/api/roles/${reqId}/request-hm-review`, {});
      toast(`Review requested — ${hmName} notified`);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const checkText = (text: string) =>
    post<CheckVerdict>(`/api/roles/${reqId}/checklist/check`, { text });

  // "suggestion created" notification → land on that exact card, flashed.
  const flashSugg = useSuggestionFocus(
    suggestions.length > 0,
    (sid) => (suggestions.some((s) => s.suggestion_id === sid) ? `sugg-${sid}` : null));

  if (error && !reqs) return <PageError message={error} />;
  if (!reqs) return <CenterLoading />;

  const hasHm = !!role.hm?.email;
  // A fresh req with no published checklist and only the seeded blank row.
  const isBlank = version === null && reqs.every((r) => !r.text.trim());
  const flagged = reqs.some((r) => r.critic_level === "warn");
  const nextVersion = (version ?? 0) + 1;
  const count = reqs.filter((r) => r.text.trim()).length;
  // Published-with-flags but never sent (approval "none") and nothing re-edited:
  // the one button sends the request instead of bumping a no-change version.
  const requestOnly = approval?.status === "none" && rowsEqual(reqs, serverRows);
  // Open HM suggestions, hung under the row each one edits (2026-07-19).
  const openSuggestions = suggestions.filter((s) => s.status === "open");
  const rowIds = new Set(reqs.map((r) => r.id ?? ""));
  const suggestionsByRow = new Map<string, Suggestion[]>();
  for (const s of openSuggestions) {
    if (!rowIds.has(s.requirement_id)) continue;
    suggestionsByRow.set(s.requirement_id, [...(suggestionsByRow.get(s.requirement_id) ?? []), s]);
  }
  const orphanSuggestions = openSuggestions.filter((s) => !rowIds.has(s.requirement_id));

  // Restore a past version: its rows land in the editor as unpublished edits, so the
  // recruiter reads them before publishing (which makes them the NEXT version).
  function restoreVersion(rows: Requirement[], from: number) {
    setReqs(rows);
    setResetSignal((n) => n + 1);
    setHistoryOpen(false);
    toast(`v${from} loaded — review it, then publish to make it v${(version ?? 0) + 1}`);
  }

  function discardEdits() {
    setReqs(serverRows);
    setResetSignal((n) => n + 1);
    clearChecklistDraft(reqId);
    setDiscardAsk(false);
    toast("Edits discarded — back to the published checklist");
  }

  return (
    // ONE column (2026-09-07). The 320px right rail took a third of the screen
    // permanently — for an activity log nobody was reading and a JD wanted
    // occasionally — while long requirement text wrapped to three lines in what was
    // left. The JD is a collapsed band on top, the publish bar sits at the end of
    // the form, and the requirements get the width.
    <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 1080, margin: "0 auto" }}>
      <JdPane jd={jd?.jd_text ?? null} loading={!jd} />
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
          <CaptionRow count={count} version={version} dirty={dirty}
                      onOpenHistory={() => setHistoryOpen(true)} right={<KindLegend />} />
          {!readOnly && (locksOn || role.locks?.checklist) && (
            <LockControl reqId={reqId} target="checklist" lock={role.locks?.checklist} label="Checklist" onChange={reload} />
          )}
        </div>
        {role.locks?.checklist && (
          <Callout variant="warn" title="Checklist locked">
            Editing and publishing are frozen while the checklist is locked. Unlock it to make changes —
            only the person who locked it, an HR Head or a Super Admin can.
          </Callout>
        )}

        {pendingDraft && (
          <Callout
            variant="warn"
            title={pendingDraft.baseVersion === version
              ? "You have unsaved checklist edits"
              : "Your saved edits are older than the published checklist"}
          >
            {pendingDraft.baseVersion === version
              ? `Saved ${dualStamp(pendingDraft.savedAt, role.tz)} — restore them, or keep what's published?`
              : `You edited on ${dualStamp(pendingDraft.savedAt, role.tz)}, but the checklist has been published again since${version ? ` (now v${version})` : ""}. Using the draft replaces what's on screen — publishing would make it the next version.`}
            <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
              <Button size="sm" onClick={restoreDraft}>
                {pendingDraft.baseVersion === version ? "Restore my edits" : "Use my draft"}
              </Button>
              <Button size="sm" variant="ghost" onClick={discardDraft}>Discard draft</Button>
            </div>
          </Callout>
        )}
        {error && <Callout variant="warn" title="Couldn't save">{error}</Callout>}

        {/* A closed requisition's checklist, and a read-only viewer's, can't be
            edited (2026-10-16): one disabled fieldset turns off every control in the
            editor — rows, the AI draft, suggestion cards — without a prop on each. */}
        <fieldset disabled={readOnly || closed} className="fieldset-plain"
                  title={closed ? "This requisition is closed — its checklist is frozen." : undefined}>
        {isBlank && (
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", margin: "4px 0" }}>
            <MagicButton onClick={draftFromJd} disabled={busy || readOnly || closed}>Draft checklist from the job description</MagicButton>
            <span style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>You edit and publish every line.</span>
          </div>
        )}

        {/* Open HM suggestions render INLINE under the row they edit (pilot
            feedback 2026-07-19 — not as one pile at the bottom); "Accept all"
            merges the lot in one pass. Publishing stays the TA's click. */}
        {hmPortalOn && openSuggestions.length > 1 && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, background: "var(--accent-blue-tint)", border: "1px solid var(--border-subtle)", borderRadius: 10, padding: "9px 14px" }}>
            <span style={{ fontSize: 12.5, fontWeight: 700, color: "var(--info-ink)" }}>
              {openSuggestions.length} hiring-manager suggestions waiting
            </span>
            <Button size="sm" variant="secondary" disabled={sBusy !== null}
                    onClick={() => void acceptAll(openSuggestions)}>
              Accept all
            </Button>
          </div>
        )}

        <ChecklistEditor
          requirements={reqs}
          onChange={setReqs}
          resetSignal={resetSignal}
          onCheckText={checkText}
          onAdded={toast}
          renderBelowRow={hmPortalOn ? (r) => (suggestionsByRow.get(r.id ?? "") ?? []).map((s) => (
            <div key={s.suggestion_id} id={`sugg-${s.suggestion_id}`}
                 style={{ animation: flashSugg === s.suggestion_id ? SUGG_FLASH : undefined, borderRadius: 12 }}>
              <SuggestionCard
                suggestion={s}
                reqTz={role.tz}
                busy={sBusy === s.suggestion_id}
                onAccept={(edited) => acceptSuggestion(s, edited)}
                onDecline={(note) => declineSuggestion(s, note)}
              />
            </div>
          )) : undefined}
        />

        {/* suggestions whose row no longer exists (edited away since) — still shown */}
        {hmPortalOn && orphanSuggestions.map((s) => (
          <div key={s.suggestion_id} id={`sugg-${s.suggestion_id}`}
               style={{ animation: flashSugg === s.suggestion_id ? SUGG_FLASH : undefined, borderRadius: 12 }}>
            <SuggestionCard
              suggestion={s}
              reqTz={role.tz}
              busy={sBusy === s.suggestion_id}
              onAccept={(edited) => acceptSuggestion(s, edited)}
              onDecline={(note) => declineSuggestion(s, note)}
            />
          </div>
        ))}
        </fieldset>

        {/* ---- the publish bar (2026-09-06, item 2): at the END of the form, where the
                next action is looked for, and sticky to the bottom of the screen while the
                form is longer than it — so it is both "at the bottom" and always in reach.
                Teal while there is something to publish. ---- */}
        {(() => {
          const publishDisabled = busy || count === 0 || !!role.locks?.checklist || readOnly || closed || !(dirty || requestOnly);
          const publishTitle = readOnly ? "Read-only view — editing stays with the recruiting team."
            : closed ? "This requisition is closed — its checklist is frozen."
            : role.locks?.checklist ? "The checklist is locked — unlock it to publish."
            : count === 0 ? "Write at least one requirement first."
            : !(dirty || requestOnly) ? "Nothing changed since the published version." : undefined;
          const flaggedN = approval?.flagged_requirement_ids.length ?? 0;
          return (
            <div className={"publish-bar" + (dirty ? " is-dirty" : "")} data-testid="publish-bar">
              <div className="publish-status">
                {/* Approval state — a flagged checklist can't be USED until the HR Head signs off. */}
                {approval && approval.status !== "approved" && (
                  <div className="publish-warn">
                    <span style={{ fontWeight: 700 }}>⚠</span>
                    <span>
                      {approval.status === "pending"
                        ? <>v{approval.version} is waiting on HR-Head approval ({flaggedN} flagged line{flaggedN === 1 ? "" : "s"}) — it can't be used until approved.</>
                        : <>v{approval.version} references sensitive factors ({flaggedN} flagged line{flaggedN === 1 ? "" : "s"}) — it needs HR-Head approval before it's used.</>}
                    </span>
                  </div>
                )}
                {dirty
                  ? <>Unpublished edits — screening still uses {version ? `v${version}` : "no checklist"} until you publish.</>
                  : requestOnly
                    ? <>v{approval?.version} is published but not yet sent for approval.</>
                    : version
                      ? <><strong>v{version}</strong> is in force. Edit any line to draft v{nextVersion}.</>
                      : <>Nothing published yet — write the requirements, then publish v1.</>}
              </div>
              <div className="publish-actions">
                {dirty && !readOnly && (
                  <button onClick={() => setDiscardAsk(true)} className="link-quiet"
                          title="Throw away the unpublished edits and go back to what is published">
                    Discard edits
                  </button>
                )}
                {approval?.status === "pending" && canEditRules && !readOnly && (
                  <button
                    onClick={() => void approveChecklist()}
                    disabled={busy}
                    style={{ height: 34, padding: "0 14px", borderRadius: 999, border: 0, background: "var(--surface-inverted)", color: "var(--on-inverted)", fontSize: 12.5, fontWeight: 700, cursor: "pointer", fontFamily: "inherit" }}
                  >
                    Approve v{approval.version} (HR Head)
                  </button>
                )}
                {hmPortalOn && (
                  <span title={hasHm ? undefined : "Assign a hiring manager on the Overview tab first"} style={{ display: "inline-flex" }}>
                    <Button size="sm" variant="secondary" onClick={() => void requestReview()} disabled={!hasHm || closed || readOnly}>
                      Request HM review
                    </Button>
                  </span>
                )}
                {/* the wrapper carries the title: a disabled button has no pointer events,
                    so the reason it is disabled would otherwise never show on hover */}
                <span title={publishTitle} style={{ display: "inline-flex" }}>
                  <Button
                    size="sm"
                    onClick={() => void (requestOnly ? requestApproval() : publish())}
                    disabled={publishDisabled}
                    title={publishTitle}
                  >
                    {requestOnly
                      ? `Send v${approval?.version} for HR Head approval`
                      : flagged ? `Send v${nextVersion} for HR Head approval` : `Approve & publish v${nextVersion}`}
                  </Button>
                </span>
              </div>
            </div>
          );
        })()}
      </div>

      {historyOpen && (
        <ChecklistVersions
          reqId={reqId}
          tz={role.tz}
          onRestore={readOnly ? undefined : restoreVersion}
          onClose={() => setHistoryOpen(false)}
        />
      )}
      {discardAsk && (
        <ConfirmModal
          spec={{
            title: "Discard your unpublished edits?",
            question: `The checklist goes back to ${version ? `v${version}, the published version` : "an empty draft"}. Nothing that was published changes.`,
            cta: "Discard edits",
            onProceed: discardEdits,
          }}
          onCancel={() => setDiscardAsk(false)}
        />
      )}
    </div>
  );
}

// ---- hiring manager (suggest mode) ------------------------------------------------

function HmChecklistPanel({ role }: { role: RoleDetail }) {
  const reqId = role.req_id;
  const me = useMe();
  const toast = useToast();
  const [reqs, setReqs] = useState<Requirement[] | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mine, setMine] = useState<Suggestion[]>([]);
  const [openRow, setOpenRow] = useState<string | null>(null);
  const [newRowOpen, setNewRowOpen] = useState(false); // "suggest a NEW requirement"
  const [busy, setBusy] = useState(false);
  const people = usePeople();
  const ta = role.created_by
    ? <Person email={role.created_by} people={people} avatar={false} />
    : <>the recruiter</>;

  const loadMine = () =>
    get<{ suggestions: Suggestion[] }>(`/api/roles/${reqId}/suggestions`)
      .then((d) => setMine(d.suggestions.filter((s) => s.actor.toLowerCase() === (me?.user ?? "").toLowerCase())))
      .catch(() => setMine([]));

  useEffect(() => {
    get<Rubric>(`/api/roles/${reqId}/rubric`)
      .then((r) => { setReqs(r.requirements); setVersion(r.version); })
      .catch((e) => setError(e.message));
    loadMine();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reqId]);

  async function send(r: Requirement, p: SuggestPayload) {
    setBusy(true);
    setError(null);
    try {
      await post(`/api/roles/${reqId}/suggestions`, {
        requirement_id: r.id ?? "",
        old_text: r.text,
        new_text: p.newText,
        note: p.note,
        old_kind: r.kind,
        new_kind: p.newKind,
        old_importance: r.importance ?? null,
        new_importance: p.newKind === "hard_gate" ? null : p.newImportance,
      });
      setOpenRow(null);
      toast("Suggestion sent to the TA");
      await loadMine();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // Suggest a brand-NEW requirement (2026-07-20): an empty requirement_id tells
  // the server it's a new row; the recruiter accepts it as an appended row and
  // publishes (same review/HR-Head-approval flow as an edit).
  async function sendNew(p: { newText: string; note: string; newKind: string; newImportance: number }) {
    setBusy(true);
    setError(null);
    try {
      await post(`/api/roles/${reqId}/suggestions`, {
        requirement_id: "", old_text: "", new_text: p.newText, note: p.note,
        new_kind: p.newKind, new_importance: p.newKind === "hard_gate" ? null : p.newImportance,
      });
      setNewRowOpen(false);
      toast("New requirement suggested to the TA");
      await loadMine();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // Withdraw an OPEN suggestion before the recruiter decides (2026-07-19).
  async function withdraw(s: Suggestion) {
    setBusy(true);
    setError(null);
    try {
      await post(`/api/suggestions/${s.suggestion_id}/withdraw`, {});
      toast("Suggestion withdrawn — suggest again any time");
      await loadMine();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // Outcome notifications (?focus=sugg:<id>) land on the ROW carrying that
  // suggestion's pending/outcome line.
  const flashSugg = useSuggestionFocus(
    mine.length > 0,
    (sid) => {
      const hit = mine.find((s) => s.suggestion_id === sid);
      return hit ? `sugg-row-${hit.requirement_id}` : null;
    });
  const flashReqId = mine.find((s) => s.suggestion_id === flashSugg)?.requirement_id ?? null;

  if (error && !reqs) return <PageError message={error} />;
  if (!reqs) return <CenterLoading />;

  const pendingFor = (id?: string) =>
    mine.find((s) => s.requirement_id === (id ?? "") && s.status === "open");
  // The latest decided outcome per row — the HM finally SEES accept/decline + note.
  const outcomeFor = (id?: string) =>
    mine.filter((s) => s.requirement_id === (id ?? "")
                       && (s.status === "accepted" || s.status === "declined"))
        .sort((a, b) => (a.ts < b.ts ? 1 : -1))[0];

  const KIND_LABEL: Record<string, string> = { nice_to_have: "Nice-to-have", must_have: "Must-have", hard_gate: "Hard gate" };

  return (
    <div style={{ display: "flex", gap: 20, alignItems: "flex-start", flexWrap: "wrap" }}>
      <div style={{ flex: "1 1 520px", minWidth: 0, display: "flex", flexDirection: "column", gap: 10 }}>
        <CaptionRow count={reqs.length} version={version} />

        {error && <Callout variant="warn" title="Couldn't send">{error}</Callout>}

        {reqs.map((r, i) => {
          const pending = pendingFor(r.id);
          const outcome = outcomeFor(r.id);
          const rowKey = r.id ?? String(i);
          return (
            <div key={rowKey} id={`sugg-row-${rowKey}`} className={`req-card kind-${r.kind}`}
                 style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 12, padding: "13px 16px",
                          animation: flashReqId === (r.id ?? "") ? SUGG_FLASH : undefined }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                {/* full wording, wrapped — never trimmed (the TA sees it whole,
                    so the HM does too; pilot feedback 2026-07-19) */}
                <div style={{
                  flex: "1 1 240px", minWidth: 240, fontSize: 13, lineHeight: 1.5,
                  border: "1px solid var(--border-strong)", borderRadius: 9, padding: "8px 11px",
                  background: "var(--white)", color: "var(--ink)", whiteSpace: "pre-wrap",
                }}>
                  {r.text}
                </div>
                {/* read-only kind — the same three-colour pill, no clicks (suggest mode) */}
                <span className="kind-seg" aria-label="Requirement kind">
                  {(["nice_to_have", "must_have", "hard_gate"] as const).map((k) => (
                    <button key={k} type="button" disabled className={(r.kind === k ? "is-on " : "") + `kind-${k}`} style={{ cursor: "default" }}>
                      {KIND_LABEL[k]}
                    </button>
                  ))}
                </span>
                {!pending && openRow === null && (
                  <button
                    onClick={() => setOpenRow(rowKey)}
                    style={{ height: 28, padding: "0 11px", borderRadius: 999, border: "1.5px solid var(--brand-green)", background: "var(--white)", color: "var(--brand-green-deep)", fontSize: 11.5, fontWeight: 700, cursor: "pointer", flex: "0 0 auto", fontFamily: "inherit" }}
                  >
                    Suggest edit
                  </button>
                )}
              </div>

              {pending && (
                <div style={{ display: "flex", alignItems: "flex-start", gap: 10, marginTop: 8, flexWrap: "wrap" }}>
                  <div style={{ flex: "1 1 240px", fontSize: 12, color: "var(--ink-soft)" }}>
                    Waiting for {ta} to accept or decline. You suggested: <em>"{pending.new_text}"</em>
                    {pending.new_kind && pending.new_kind !== pending.old_kind &&
                      <> · {KIND_LABEL[pending.new_kind]}</>}
                    {pending.new_importance != null && pending.new_importance !== pending.old_importance &&
                      <> · importance {pending.new_importance}</>}
                  </div>
                  <button
                    onClick={() => void withdraw(pending)}
                    disabled={busy}
                    style={{ height: 26, padding: "0 10px", borderRadius: 999, border: "1px solid var(--border-strong)", background: "var(--white)", color: "var(--ink-soft)", fontSize: 11, fontWeight: 700, cursor: "pointer", fontFamily: "inherit", flex: "0 0 auto" }}
                  >
                    Withdraw
                  </button>
                </div>
              )}

              {/* the decided history — outcome + the recruiter's note, at last visible */}
              {!pending && outcome && (
                <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 8, fontSize: 12, flexWrap: "wrap" }}>
                  <span style={{ fontWeight: 700, padding: "2px 9px", borderRadius: 999,
                    background: outcome.status === "accepted" ? "var(--brand-green-tint)" : "var(--neutral-150)",
                    color: outcome.status === "accepted" ? "var(--brand-green-deep)" : "var(--ink-soft)" }}>
                    {outcome.status === "accepted" ? "Accepted" : "Declined"}
                  </span>
                  <span style={{ color: "var(--ink-soft)" }}>
                    “{outcome.new_text}”{outcome.decided_note ? <> — {outcome.decided_by}: “{outcome.decided_note}”</> : null}
                  </span>
                </div>
              )}

              {!pending && openRow === rowKey && (
                <SuggestComposer
                  requirement={r}
                  busy={busy}
                  onSend={(p) => send(r, p)}
                  onCancel={() => setOpenRow(null)}
                />
              )}
            </div>
          );
        })}

        {/* Suggest a brand-new requirement (2026-07-20) — same review flow */}
        {newRowOpen ? (
          <NewRequirementComposer busy={busy} onSend={sendNew} onCancel={() => setNewRowOpen(false)} />
        ) : openRow === null ? (
          <button
            onClick={() => setNewRowOpen(true)}
            style={{ alignSelf: "flex-start", height: 34, padding: "0 14px", borderRadius: 999, border: "1.5px dashed var(--brand-green)", background: "var(--white)", color: "var(--brand-green-deep)", fontSize: 12.5, fontWeight: 700, cursor: "pointer", fontFamily: "inherit" }}
          >
            + Suggest a new requirement
          </button>
        ) : null}
      </div>

      <div className="checklist-rail" style={{ flex: "0 0 320px", display: "flex", flexDirection: "column", gap: 14 }}>
        <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 14, padding: "16px 18px" }}>
          <div style={{ fontSize: 12.5, lineHeight: 1.55, color: "var(--ink-soft)" }}>
            <strong style={{ color: "var(--ink)" }}>Suggest mode</strong> — propose edits on any
            line; {ta} reviews and publishes.
          </div>
        </div>
        <ActivityRail reqId={reqId} />
      </div>
    </div>
  );
}

/** The HM's composer for a brand-new requirement (2026-07-20) — text + kind +
 *  importance, sent to the recruiter to accept-as-append and publish. */
function NewRequirementComposer({ busy, onSend, onCancel }: {
  busy: boolean;
  onSend: (p: { newText: string; note: string; newKind: string; newImportance: number }) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState("");
  const [kind, setKind] = useState("nice_to_have");
  const [importance, setImportance] = useState(3);
  const [note, setNote] = useState("");
  const KINDS = [["nice_to_have", "Nice-to-have"], ["must_have", "Must-have"], ["hard_gate", "Hard gate"]] as const;
  return (
    <div style={{ background: "var(--white)", border: "1px solid var(--brand-green)", borderRadius: 12, padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ fontSize: 12.5, fontWeight: 700, color: "var(--ink)" }}>Suggest a new requirement</div>
      <textarea className="input" rows={2} placeholder="e.g. CA / CPA qualification" value={text}
        onChange={(e) => setText(e.target.value)} style={{ resize: "vertical", minHeight: 40 }} />
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <span style={{ display: "inline-flex", border: "1px solid var(--border-subtle)", borderRadius: 999, overflow: "hidden", fontSize: 11, fontWeight: 700 }}>
          {KINDS.map(([k, label]) => (
            <button key={k} onClick={() => setKind(k)}
              style={{ padding: "6px 11px", border: 0, cursor: "pointer", fontFamily: "inherit", background: kind === k ? (k === "hard_gate" ? "var(--surface-inverted)" : "var(--brand-green)") : "transparent", color: kind === k ? "#fff" : "var(--ink-muted)" }}>
              {label}
            </button>
          ))}
        </span>
        {kind !== "hard_gate" && (
          <label style={{ fontSize: 11.5, color: "var(--ink-soft)", display: "inline-flex", alignItems: "center", gap: 6 }}>
            Importance
            <select value={importance} onChange={(e) => setImportance(Number(e.target.value))}
              style={{ height: 30, borderRadius: 8, border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink)", fontFamily: "inherit" }}>
              {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        )}
      </div>
      <input className="input" placeholder="Why (optional) — the recruiter sees this note…" value={note}
        onChange={(e) => setNote(e.target.value)} />
      <div style={{ display: "flex", gap: 8 }}>
        <button
          onClick={() => text.trim() && onSend({ newText: text.trim(), note: note.trim(), newKind: kind, newImportance: importance })}
          disabled={busy || !text.trim()}
          style={{ height: 32, padding: "0 14px", borderRadius: 8, border: "1px solid var(--brand-green)", background: "var(--brand-green-tint)", color: "var(--brand-green-deep)", fontSize: 12, fontWeight: 700, cursor: busy || !text.trim() ? "default" : "pointer", opacity: busy || !text.trim() ? 0.55 : 1, fontFamily: "inherit" }}
        >
          Suggest to the recruiter
        </button>
        <button onClick={onCancel} style={{ height: 32, padding: "0 12px", borderRadius: 8, border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink-soft)", fontSize: 12, fontWeight: 700, cursor: "pointer", fontFamily: "inherit" }}>
          Cancel
        </button>
      </div>
    </div>
  );
}

export default function ChecklistTab() {
  const { role, reload } = useWorkspace();
  const me = useMe();
  const hmPortalOn = useFeatureFlag("hm_portal") ?? false;

  const hm = isHmActive(me);
  return (
    <Page max={1240}>
      {hm ? (
        <HmChecklistPanel role={role} />
      ) : (
        <ChecklistPanel role={role} reload={reload} hmPortalOn={hmPortalOn} />
      )}
    </Page>
  );
}
