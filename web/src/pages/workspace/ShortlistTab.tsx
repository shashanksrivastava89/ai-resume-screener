// The workspace's Shortlist tab — the SECOND PASS over the decisions.
//
// 2026-09-07 (pilot feedback item 16). Everyone here was already decided on, so the
// question this screen answers is "do I still think so?" — which means every row has
// to lead back to the evidence. A row is now a link into that candidate's drawer on
// the Review tab, and the drawer offers a way back here. The checkboxes are gone:
// selecting résumés to download was a second, unrelated selection model living on
// top of the decision buckets, so each bucket downloads itself and one button takes
// the lot. Every downloaded file carries its own record — name, requisition,
// decision, date in the filename, and the same in the file's document properties.
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Page } from "../../layout/AppShell";
import { Button, Callout, CenterLoading, PageError } from "../../components/ui";
import { ConfirmModal, useToast } from "../../components/overlays";
import { IconDownload } from "../../components/icons";
import { useFeatureFlag, useFetch } from "../../hooks";
import { downloadFile, post } from "../../api";
import { tabPath, useWorkspace } from "./context";
import { Avatar } from "../../people";
import { TAKE_LABEL, type Take } from "../../components/HmTakeButtons";
import type { ReviewResponse, ReviewCard, RoleDetail } from "../../types";

// The decision buckets wear the decision hues (the same three the Review tab's
// pills and the dashboard's popup use): green forward · amber discuss · slate not.
type Hue = "green" | "amber" | "slate" | "blue" | "violet";
const HUE_INK: Record<Hue, string> = {
  green: "var(--hue-green-ink)", amber: "var(--hue-amber-ink)", slate: "var(--hue-slate-ink)",
  blue: "var(--hue-blue-ink)", violet: "var(--hue-violet-ink)",
};
const HUE_TINT: Record<Hue, string> = {
  green: "var(--hue-green-tint)", amber: "var(--hue-amber-tint)", slate: "var(--hue-slate-tint)",
  blue: "var(--hue-blue-tint)", violet: "var(--hue-violet-tint)",
};
const HUE_SOLID: Record<Hue, string> = {
  green: "var(--hue-green-solid)", amber: "var(--hue-amber-solid)", slate: "var(--hue-slate-solid)",
  blue: "var(--hue-blue-solid)", violet: "var(--hue-violet-solid)",
};

/** The hiring manager's take beside the TA's decision — the same mapping the
 *  Review tab's HM chip uses: strong yes green · yes blue · no slate. */
function TakeChip({ take, hmName }: { take: string; hmName?: string }) {
  const hue: Hue = take === "no" ? "slate" : take === "yes" ? "blue" : "green";
  const label = TAKE_LABEL[take as Take] ?? take;
  return (
    <span title={hmName ? `${hmName}'s take` : "The hiring manager's take"}
      style={{ fontSize: 11, fontWeight: 700, padding: "3px 9px", borderRadius: 999, whiteSpace: "nowrap",
               background: HUE_TINT[hue], color: HUE_INK[hue] }}>
      HM · {label}
    </span>
  );
}

/** A candidate line: the whole row opens that candidate's drawer on the Review tab,
 *  because a second pass means re-reading the evidence, not re-reading a name. The
 *  drawer arrives with a "Back to shortlist" button (Review reads ?from=shortlist). */
function CandidateLine({ c, onOpen, muted = false }: {
  c: ReviewCard; onOpen: (id: string) => void; muted?: boolean;
}) {
  return (
    <button
      type="button"
      className="sl-row"
      onClick={() => onOpen(c.candidate_id)}
      aria-label={`Open ${c.name}`}
      title={c.score != null ? `Open ${c.name} — evidence, score and your note` : `Open ${c.name}`}
    >
      <Avatar email={c.candidate_id} name={c.name} size="sm" />
      <span style={{ flex: "1 1 auto", minWidth: 0, textAlign: "left", fontSize: 13.5, fontWeight: 700, color: muted ? "var(--ink-soft)" : "var(--ink)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.name}</span>
      {c.hm_take && <TakeChip take={c.hm_take.take} hmName={c.hm_take.hm_name} />}
      {c.score != null && <span style={{ fontSize: 12.5, fontWeight: 700, color: muted ? "var(--ink-soft)" : "var(--ink)", fontVariantNumeric: "tabular-nums" }}>{c.score}</span>}
      <span aria-hidden="true" className="sl-chev">›</span>
    </button>
  );
}

function Bucket({ title, hue, cards, empty, next, onOpen, onDownload, busy }: {
  title: string; hue: Hue; cards: ReviewCard[]; empty: string; next: string;
  onOpen: (id: string) => void;
  onDownload?: (ids: string[], what: string) => void;
  busy?: boolean;
}) {
  return (
    <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderTop: `4px solid ${HUE_SOLID[hue]}`, borderRadius: 16, overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 18px", background: HUE_TINT[hue] }}>
        <span style={{ fontSize: 15, fontWeight: 700, color: HUE_INK[hue] }}>{title}</span>
        <span style={{ fontSize: 12.5, fontWeight: 700, color: HUE_INK[hue], background: "var(--white)", borderRadius: 999, padding: "2px 10px" }}>{cards.length}</span>
        {/* Each bucket downloads itself — no second selection model on top of the
            decisions people already made (item 16). */}
        {onDownload && cards.length > 0 && (
          <button
            className="sl-dl"
            onClick={() => onDownload(cards.map((c) => c.candidate_id), title)}
            disabled={busy}
            title={`Download the ${cards.length} résumé${cards.length === 1 ? "" : "s"} in ${title}, each stamped with the decision`}
          >
            <IconDownload size={13} /> Download
          </button>
        )}
      </div>
      {cards.length === 0 && <div style={{ padding: "20px 18px", fontSize: 13, color: "var(--ink-soft)" }}>{empty}</div>}
      {cards.map((c) => <CandidateLine key={c.candidate_id} c={c} onOpen={onOpen} />)}
      {cards.length > 0 && (
        // One quiet line instead of a stack of disabled buttons: what comes next,
        // and that it is not here yet.
        <div style={{ padding: "10px 18px", borderTop: "1px solid var(--neutral-150)", fontSize: 12, color: "var(--ink-muted)" }}>
          Next: {next} — arrives with the iCIMS integration.
        </div>
      )}
    </div>
  );
}

export function ShortlistPanel({ role }: { role: RoleDetail }) {
  const { readOnly, closed } = useWorkspace();
  const reqId = role.req_id;
  const nav = useNavigate();
  const toast = useToast();
  const { data, error, loading, reload } = useFetch<ReviewResponse>(`/api/roles/${reqId}/review`);
  const [rescreenOpen, setRescreenOpen] = useState(false);
  const [shareBusy, setShareBusy] = useState(false);
  const [dlBusy, setDlBusy] = useState(false);

  // A row leads BACK to the evidence: the Review tab opens that candidate's drawer
  // and shows a "Back to shortlist" button (?from=shortlist).
  const openCandidate = (candidateId: string) =>
    nav(`/reqs/${reqId}/review?focus=cand:${candidateId}&open=1&from=shortlist`);

  const buckets = useMemo(() => {
    const cards = data?.cards ?? [];
    return {
      forward: cards.filter((c) => c.decided === "forward"),
      discuss: cards.filter((c) => c.decided === "discuss"),
      not: cards.filter((c) => c.decided === "not"),
      // Still waiting on a decision (2026-07-19) — its own group so the TA sees
      // at a glance how much of the batch the shortlist doesn't cover yet.
      undecided: cards.filter((c) => !c.decided),
      // The HM disagreed with a Not-a-fit (2026-07-19): a positive take pulls
      // the candidate back onto the shortlist as its own visible group.
      hmPicks: cards.filter((c) => c.decided === "not"
        && (c.hm_take?.take === "yes" || c.hm_take?.take === "strong_yes")),
    };
  }, [data]);

  const hasHm = !!role.hm?.email;
  const decidedCount = (data?.cards ?? []).filter((c) => !!c.decided).length;
  // HR's UAT ask (2026-10-05) was "share shortlisted CVs with the hiring manager" —
  // the feature existed, but every reason it could not be used hid in the tooltip of
  // a DISABLED button, which never shows. Each reason is now said on the page.
  const portalOn = useFeatureFlag("hm_portal");
  const hmLabel = role.hm?.name || role.hm?.email || "the hiring manager";
  const reviewLink = `${window.location.origin}${tabPath(reqId, "review")}`;
  const [linkCopied, setLinkCopied] = useState(false);
  async function copyReviewLink() {
    try {
      await navigator.clipboard.writeText(reviewLink);
      setLinkCopied(true);
      window.setTimeout(() => setLinkCopied(false), 2400);
    } catch {
      toast(`Copy this link for ${hmLabel}: ${reviewLink}`);
    }
  }
  // Snapshot semantics for DECISIONS (2026-07-21): the HM sees decisions AS OF
  // the last share, so a decision change (or a newly-added candidate) enables
  // "Send updates". Comments reach the HM live and are NOT part of this.
  const updates = data?.hm_updates ?? null;
  const updatesPending = !!updates?.pending;
  const nDecChanged = updates?.decisions?.length ?? 0;
  const nNewCands = updates?.new_candidates?.length ?? 0;

  async function shareWithHm() {
    setShareBusy(true);
    const resend = !!data?.hm_share;
    try {
      await post(`/api/roles/${reqId}/share-hm`);
      toast(resend
        ? `Updates sent to ${role.hm?.name || role.hm?.email} — their view now matches yours`
        : `Shared with ${role.hm?.name || role.hm?.email} — they see the shortlist in their Review tab`);
      reload();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setShareBusy(false);
    }
  }

  if (loading) return <CenterLoading />;
  if (error || !data?.ready) return <PageError message={error || "This role hasn't been screened yet."} />;

  // A shortlist is nothing but the team's decisions (2026-10-21), so outside the team
  // it says so instead of listing everyone as undecided.
  if (data.decisions_visible === false) {
    return (
      <div style={{ maxWidth: 640, margin: "40px auto" }}>
        <Callout variant="info" title="The shortlist is the requisition team's">
          {data.decisions_note}
        </Callout>
      </div>
    );
  }

  // P51 (the Shortlist gate): a shortlist is only final on CURRENT rules — when
  // the config moved since this screen ran, the step forward is a re-screen.
  if (data.staleness) {
    const decidedCount = buckets.forward.length + buckets.discuss.length + buckets.not.length;
    return (
      <div style={{ maxWidth: 640, margin: "40px auto" }}>
        <Callout variant="warn" title="The rules changed after this screen ran">
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span>{data.staleness.summary} A shortlist has to stand on current rules, so
              re-screen before taking candidates forward.</span>
            {data.staleness.details.map((d, i) => (<span key={i}>{d.headline}</span>))}
            <span><Button size="sm" onClick={() => setRescreenOpen(true)}>Re-screen with current rules</Button></span>
          </div>
        </Callout>
        {rescreenOpen && (
          <ConfirmModal
            spec={{
              title: "The ground shifted under these scores",
              text: data.staleness.summary,
              reason: data.staleness.details.map((d) => d.headline).join(" · ")
                + ". Every scoring factor was checked against this run's stamps.",
              question: `${decidedCount === 1 ? "1 recorded decision stays" : `${decidedCount} recorded decisions stay`} in the audit trail. Re-screening resets this requisition to Screen — the re-screen itself is the ordinary screen click on the Candidates tab.`,
              cta: "Go to Candidates and re-screen",
              onProceed: () => nav(`/reqs/${reqId}/candidates`),
            }}
            onCancel={() => setRescreenOpen(false)}
          />
        )}
      </div>
    );
  };

  const allIds = (data?.cards ?? []).map((c) => c.candidate_id);

  async function exportCsv() {
    // Server-built so the "Resume file" column matches the ZIP layout exactly
    // (bucket folder + filename) — the client never sees the résumé filenames.
    try {
      await downloadFile(`/api/roles/${reqId}/shortlist/export.csv`, {
        fallbackName: `shortlist-${data?.req_code || reqId}.csv`,
      });
    } catch (e) {
      toast((e as Error).message);
    }
  }

  // ONE download path, called with whichever set of ids the caller means: a bucket,
  // or everyone. Each résumé arrives named for its decision and stamped inside.
  async function downloadZip(ids: string[], what: string) {
    if (ids.length === 0) return;
    setDlBusy(true);
    try {
      await downloadFile(`/api/roles/${reqId}/shortlist/download`, {
        method: "POST",
        body: { candidate_ids: ids },
        fallbackName: `shortlist-${data?.req_code || reqId}.zip`,
      });
      toast(`${ids.length} résumé${ids.length === 1 ? "" : "s"} downloaded — ${what}`);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setDlBusy(false);
    }
  }

  return (
    <>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 20, flexWrap: "wrap", margin: "0 0 6px" }}>
        <div>
          <h2 style={{ fontSize: 21, letterSpacing: "-0.01em", margin: "0 0 4px" }}>Shortlisted candidates</h2>
          <p style={{ fontSize: 13.5, color: "var(--ink-soft)", margin: 0 }}>
            Your decisions, grouped. Nothing is sent anywhere on its own.
          </p>
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          {/* the real share: until clicked, the HM's Review tab is empty. After
              it, the HM sees the DECISION snapshot — a decision change or a newly
              added candidate re-enables "Send updates". Comments are live. */}
          <Button
            onClick={shareWithHm}
            disabled={readOnly || closed || shareBusy || !hasHm || decidedCount === 0 || (!!data.hm_share && !updatesPending)}
            title={readOnly ? "Read-only view — sharing stays with the recruiting team"
              : closed ? "This requisition is closed — sharing is frozen"
              : !hasHm ? "Add a hiring manager (with an email) on the Overview tab first"
              : decidedCount === 0 ? "Record at least one decision first"
              : data.hm_share && !updatesPending
                ? "Nothing new to send — the hiring manager sees the latest shared decisions (comments reach them live)"
              : data.hm_share && updatesPending
                ? [nDecChanged ? `${nDecChanged} decision change${nDecChanged === 1 ? "" : "s"}` : "",
                   nNewCands ? `${nNewCands} new candidate${nNewCands === 1 ? "" : "s"}` : ""]
                    .filter(Boolean).join(" · ") + " since the last share"
                : undefined}
          >
            {!data.hm_share ? "Share with hiring manager"
              : updatesPending ? "Send updates to hiring manager"
              : "Shared with hiring manager ✓"}
          </Button>
          <Button
            variant="secondary"
            onClick={() => downloadZip(allIds, "everyone on this shortlist")}
            disabled={allIds.length === 0 || dlBusy}
            title="One ZIP, foldered by decision, with the shortlist CSV — every file named and stamped with its decision"
          >
            <IconDownload size={15} /> {dlBusy ? "Preparing…" : `Download all${allIds.length ? ` (${allIds.length})` : ""}`}
          </Button>
          <Button variant="secondary" onClick={exportCsv}><IconDownload size={15} /> Export (CSV)</Button>
        </div>
      </div>
      {!readOnly && (!hasHm || decidedCount === 0 || portalOn === false) && (
        <div role="note" style={{ marginTop: 8, fontSize: 12.5, color: "var(--hue-blue-ink)", background: "var(--hue-blue-tint)", borderRadius: 10, padding: "8px 12px" }}>
          {!hasHm ? (
            <>To share this shortlist, add a hiring manager first — <Link to={tabPath(reqId, "overview")} style={{ fontWeight: 700, color: "inherit" }}>on the Overview tab</Link>. Pick them from the directory so the address matches the one they sign in with.</>
          ) : decidedCount === 0 ? (
            <>Record at least one decision in Review, then share the shortlist with {hmLabel}.</>
          ) : (
            <>The hiring-manager portal is switched off, so {hmLabel} can't open what you share yet. A Super Admin can turn it on under Governance → Feature flags.</>
          )}
        </div>
      )}
      {allIds.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 12.5, color: "var(--ink-soft)" }}>
          Open any candidate to re-read the evidence. Downloads fold each résumé under
          its decision and name it with the requisition, the call and the date — the same
          record is written into the file’s own properties.
        </div>
      )}
      {data.hm_share && (
        <div style={{ fontSize: 12.5, color: "var(--ink-soft)", marginTop: 4 }}>
          Shared with {role.hm?.name || role.hm?.email} on{" "}
          {data.hm_share.ts ? new Date(data.hm_share.ts).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—"}
          {" "}— they see the shortlist as of that share
          {updatesPending
            ? "; you've made changes since, so send updates when you're ready."
            : "; decisions and comments you add later travel with “Send updates”. A re-screen clears the share."}
          {/* No email leaves the app until mail is approved (the bell rings in-app
              only), so the recruiter gets the link to send themselves. */}
          {" "}{hmLabel} isn't emailed —{" "}
          <button type="button" onClick={() => void copyReviewLink()}
                  style={{ border: 0, background: "none", padding: 0, fontFamily: "inherit", fontSize: "inherit", fontWeight: 700, color: "var(--brand-green-deep)", cursor: "pointer", textDecoration: "underline", textUnderlineOffset: 2 }}>
            {linkCopied ? "link copied" : "copy the link to send them"}
          </button>.
        </div>
      )}

      <div style={{ height: 20 }} />

      {/* HM disagreements surface as first-class shortlist items */}
      {buckets.hmPicks.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <Bucket
            title="Hiring manager disagrees — from “Not a fit”"
            hue="violet"
            cards={buckets.hmPicks}
            empty=""
            next="discuss with the hiring manager"
            onOpen={openCandidate}
          />
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 20, alignItems: "start" }}>
        <Bucket title="Move forward" hue="green" cards={buckets.forward} empty="No one yet — choose “Move forward” in Review." next="invite to interview" onOpen={openCandidate} onDownload={downloadZip} busy={dlBusy} />
        <Bucket title="Discuss" hue="amber" cards={buckets.discuss} empty="No one yet — choose “Discuss” in Review." next="a second reviewer or a discussion list" onOpen={openCandidate} onDownload={downloadZip} busy={dlBusy} />
      </div>

      <CollapsedStrip
        title={`Not a fit — ${buckets.not.length} candidate${buckets.not.length === 1 ? "" : "s"}`}
        sub="Nobody is rejected automatically — update them when you're ready, or keep them on file."
        hue="slate"
        cards={buckets.not}
        onOpen={openCandidate}
        onDownload={downloadZip}
        busy={dlBusy}
      />

      {/* Undecided (2026-07-19): the part of the batch the shortlist doesn't
          cover yet — decisions happen on the Review tab. */}
      <CollapsedStrip
        title={`Undecided — ${buckets.undecided.length} candidate${buckets.undecided.length === 1 ? "" : "s"}`}
        sub="Still waiting for a decision on the Review tab."
        hue="amber"
        cards={buckets.undecided}
        onOpen={openCandidate}
      />
    </>
  );
}

/** A quiet, expandable strip for the groups that aren't the shortlist's point
 *  (Not-a-fit, Undecided) — a count at a glance, names on demand. */
function CollapsedStrip({ title, sub, hue, cards, onOpen, onDownload, busy }: {
  title: string; sub: string; hue: Hue; cards: ReviewCard[];
  onOpen: (id: string) => void;
  onDownload?: (ids: string[], what: string) => void;
  busy?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderLeft: `4px solid ${HUE_SOLID[hue]}`, borderRadius: 16, marginTop: 16, overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "14px 18px" }}>
        <button
          onClick={() => setOpen((v) => !v)}
          disabled={cards.length === 0}
          style={{ flex: "1 1 auto", minWidth: 0, textAlign: "left", display: "flex", alignItems: "center", gap: 12, padding: 0, background: "none", border: 0, cursor: cards.length ? "pointer" : "default", fontFamily: "inherit" }}
        >
          <span style={{ minWidth: 0 }}>
            <span style={{ display: "block", fontSize: 14.5, fontWeight: 700, color: HUE_INK[hue] }}>{title}</span>
            <span style={{ display: "block", fontSize: 12.5, color: "var(--ink-soft)" }}>{sub}</span>
          </span>
          {cards.length > 0 && (
            <span style={{ marginLeft: "auto", fontSize: 12, fontWeight: 700, color: "var(--brand-green-deep)", flex: "0 0 auto" }}>
              {open ? "Hide" : "Show"}
            </span>
          )}
        </button>
        {onDownload && cards.length > 0 && (
          <button className="sl-dl" onClick={() => onDownload(cards.map((c) => c.candidate_id), title)} disabled={busy}
                  title={`Download the ${cards.length} résumé${cards.length === 1 ? "" : "s"} here`}>
            <IconDownload size={13} /> Download
          </button>
        )}
      </div>
      {open && cards.map((c) => <CandidateLine key={c.candidate_id} c={c} onOpen={onOpen} muted />)}
    </div>
  );
}

export default function ShortlistTab() {
  const { role } = useWorkspace();
  return (
    <Page max={1080}>
      <ShortlistPanel role={role} />
    </Page>
  );
}
