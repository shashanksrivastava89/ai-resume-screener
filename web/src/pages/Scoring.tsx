// The Scoring model page (v3 design): four tabs — How it works · The score ·
// Bias critic · Versions — over ONE staged draft, with the sticky formula
// strip, the live "Watch it work" calculator (the real engine under DRAFT
// values), and the dark draft bar that submits everything as one change
// proposal for a second leader.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { post } from "../api";
import { invalidate, useFetch } from "../hooks";
import { useMe, useMeRefresh } from "../MeContext";
import { CenterLoading, PageError } from "../components/ui";
import { useToast } from "../components/overlays";
import type { ChangeRequest, EffectiveModelView, JurisdictionsResponse, PreviewResponse, ScoringDoc, ScoringModelRead } from "../types";
import FormulaStrip from "../components/scoring/FormulaStrip";
import HowTab from "../components/scoring/HowTab";
import ScoreTab from "../components/scoring/ScoreTab";
import BatchTab from "../components/scoring/BatchTab";
import CriticTab from "../components/scoring/CriticTab";
import VersionsTab from "../components/scoring/VersionsTab";
import Calculator, { partsFromPreview } from "../components/scoring/Calculator";
import DraftBar from "../components/scoring/DraftBar";
import { projectDoc } from "../components/scoring/lens";
import {
  SAMPLE_ADJUSTMENTS, SAMPLE_DOWNGRADED, SAMPLE_EXTRACTION, SAMPLE_PREF,
  SAMPLE_RUBRIC, SAMPLE_SUSPENDED, countChanges, draftWarnings, signalName,
} from "../components/scoring/model";

const TAB_KEYS = ["how", "score", "batch", "critic", "versions"] as const;
type TabKey = (typeof TAB_KEYS)[number];

// The InfoDot texts (the design's mkTip strings, verbatim).
const TIPS: Record<string, string> = {
  deterministic: "Plain arithmetic over the approved checklist — no AI, no randomness. Same inputs, same number, re-derivable months later for any auditor or candidate.",
  cap: "The most points one signal can add (or subtract), no matter how strong the evidence reads. Caps keep any single story from swinging a hire.",
  clamp: "After everything is added up, the score is held inside the floor and ceiling (0–100 by default) so one wild input can’t blow past the scale.",
  weights: "Each checklist line’s importance. Weights always re-balance to add up to 100%, so the base stays out of 100.",
  floor: "A protection treated as law-adjacent, drawn from the global reference set (GDPR, Title VII, and each location's own act). A location's hiring-rules pack may ADD floor topics — never remove one. Removing one here needs the approver to acknowledge the exposure by name.",
  suspend: "Removed from the score by code, not by trust in a model — and kept visible so nothing disappears quietly.",
  prefCap: "Personal preferences nudge, never decide — each is held to a small range set here in the scoring model.",
  confidence: "A read on the record, not the person: how much the text extraction (OCR) guessed, and how many of the AI’s claims the evidence-check rejected.",
  direction: "“+ only” can add, never subtract. “±” can move either way within the cap — used only where evidence can legitimately cut both ways.",
};

function buildSpec(doc: ScoringDoc) {
  const adjustments: Record<string, unknown>[] = [];
  for (const a of SAMPLE_ADJUSTMENTS) {
    const s = doc.signals.find((x) => x.id === a.signalId);
    if (!s || !s.enabled_default || s.retired) continue;
    adjustments.push({ label: signalName(s), points: a.points, cap_points: s.cap_points, direction: s.direction, verdict: a.verdict });
  }
  for (const a of SAMPLE_SUSPENDED) {
    const s = doc.signals.find((x) => x.id === a.signalId);
    adjustments.push({ label: s ? signalName(s) : a.signalId, points: a.points, cap_points: s?.cap_points ?? 3, direction: s?.direction ?? "both", verdict: "suspend" });
  }
  return {
    requirements: SAMPLE_RUBRIC.map((r) => ({ label: r.label, kind: r.kind, weight: r.weight, match: r.match })),
    adjustments,
    preferences: [{ label: SAMPLE_PREF.label, points: SAMPLE_PREF.points }],
    extraction_confidence: SAMPLE_EXTRACTION,
    downgraded_findings: SAMPLE_DOWNGRADED,
  };
}

function stripVersion(doc: ScoringDoc): Record<string, unknown> {
  const { version: _v, schema: _s, ...rest } = doc;
  return rest as Record<string, unknown>;
}

function usePreview(doc: ScoringDoc | null, delay = 250): PreviewResponse | null {
  const [result, setResult] = useState<PreviewResponse | null>(null);
  const key = doc ? JSON.stringify(doc) : null;
  useEffect(() => {
    if (!doc || !key) return;
    let live = true;
    const timer = window.setTimeout(() => {
      post<PreviewResponse>("/api/scoring/preview", { model: stripVersion(doc), candidate: buildSpec(doc) })
        .then((r) => { if (live) setResult(r); })
        .catch(() => { /* an incoherent mid-edit draft — keep the last good result */ });
    }, delay);
    return () => { live = false; window.clearTimeout(timer); };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return result;
}

export default function Scoring() {
  const { tab: tabParam } = useParams();
  const navigate = useNavigate();
  const me = useMe();
  const refreshMe = useMeRefresh();
  const toast = useToast();
  const tab: TabKey = (TAB_KEYS as readonly string[]).includes(tabParam ?? "") ? (tabParam as TabKey) : "how";

  const { data, error } = useFetch<ScoringModelRead>("/api/scoring-model");
  const { data: changesData, reload: reloadChanges } = useFetch<{ changes: ChangeRequest[] }>(
    "/api/changes?status=pending");
  const pending = (changesData?.changes ?? []).filter((c) => c.kind === "scoring_model");
  // Config-to-config staleness: signals that conflict with a jurisdiction's floor.
  const { data: conflictsData } = useFetch<{ conflicts: { signal_id: string; signal_name: string; jurisdiction: string; pattern_name: string; reason: string }[] }>(
    "/api/scoring/conflicts");
  const conflicts = conflictsData?.conflicts ?? [];

  const [draft, setDraft] = useState<ScoringDoc | null>(null);
  const [draftReason, setDraftReason] = useState("");
  const [ackFloor, setAckFloor] = useState<string[]>([]);
  const [cardOpen, setCardOpen] = useState<Record<string, boolean>>({});
  const [lastCard, setLastCard] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const flashTimer = useRef<number | null>(null);

  // WHICH JURISDICTION AM I LOOKING AT? (2026-09-12). There is ONE model; a
  // location's hiring-rules pack turns the dial. `lens` null = the global model,
  // which is the only view that can be EDITED — changing what a signal does in
  // one location is a change to that location's pack, on the Jurisdictions page.
  const [lens, setLens] = useState<string | null>(null);
  const { data: packs } = useFetch<JurisdictionsResponse>("/api/jurisdictions");
  const { data: applied, error: lensError } = useFetch<EffectiveModelView>(
    lens ? `/api/scoring-model/effective?jurisdiction=${encodeURIComponent(lens)}` : null);

  const published = data?.model ?? null;
  // Under a lens the page shows the EFFECTIVE values — the ones a run in that
  // location would use — so there is no second rendering path to drift from the
  // engine. A staged draft is kept in state but never shown there.
  const projected = published && applied ? projectDoc(published, applied) : published;
  const doc = lens ? projected : (draft ?? published);
  // A staged edit, on the view that can be edited. Under a lens the shown doc is
  // a PROJECTION of published values, which is not an edit and must never bring
  // up the draft bar.
  const dirty = !lens && !!draft && !!published
    && JSON.stringify(draft) !== JSON.stringify(published);

  const stage = useCallback((mutate: (d: ScoringDoc) => void) => {
    if (lens) {
      toast("This view is read-only. Switch to the global model to edit it, or change this location's settings on the Jurisdictions page.");
      return;
    }
    setDraft((current) => {
      const base = current ?? published;
      if (!base) return current;
      const next = JSON.parse(JSON.stringify(base)) as ScoringDoc;
      mutate(next);
      return next;
    });
  }, [published, lens, toast]);

  const goSection = useCallback((toTab: string, card: string | null) => {
    navigate(`/scoring/${toTab}`, { replace: true });
    const key = card ?? "criticIntro";
    if (card) {
      setCardOpen((m) => ({ ...m, [card]: true }));
      setLastCard(card);
    }
    setFlash(key);
    if (flashTimer.current) window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlash(null), 1600);
    window.setTimeout(() => {
      document.getElementById(`sec-${key}`)?.scrollIntoView?.({ behavior: "smooth", block: "center" });
    }, 60);
  }, [navigate]);

  const preview = usePreview(doc);
  const publishedPreview = usePreview(published, 0);

  const dirtySections = useMemo(() => {
    if (!published || !doc) return { credits: false, signals: false, prefs: false, conf: false };
    return {
      credits: JSON.stringify(doc.coverage) !== JSON.stringify(published.coverage),
      signals: JSON.stringify(doc.signals) !== JSON.stringify(published.signals)
        || doc.adjustments.total_cap_points !== published.adjustments.total_cap_points,
      prefs: JSON.stringify(doc.preferences) !== JSON.stringify(published.preferences),
      conf: JSON.stringify(doc.confidence) !== JSON.stringify(published.confidence),
    };
  }, [doc, published]);

  if (error) return <div style={{ padding: 40 }}><PageError message={error} /></div>;
  if (!data || !published || !doc) return <CenterLoading label="Loading the scoring model…" />;

  const lensName = lens
    ? (packs?.jurisdictions ?? []).find((j) => j.code === lens)?.name ?? lens
    : "";
  const warnings = dirty ? draftWarnings(published, doc, data.knobs) : [];
  const stagedCount = dirty ? countChanges(published, doc, data.knobs.map((k) => k.path)) : 0;
  const hlMap: Record<string, string> = { credits: "base", signals: "net", prefs: "pref" };
  const calcHighlight = lastCard && cardOpen[lastCard] ? hlMap[lastCard] ?? null : null;
  const sinceLabel = data.versions.find((v) => v.version === data.version)?.ts;
  const since = sinceLabel
    ? new Date(sinceLabel).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" })
    : "the pilot rollout";

  const submit = async () => {
    if (!dirty) { toast("No changes staged yet"); return; }
    if (!draftReason.trim()) { toast("Reasoning is required — the approver and auditors read it verbatim"); return; }
    setBusy(true);
    try {
      await post("/api/changes", {
        kind: "scoring_model",
        target: "scoring_model",
        payload: { model: stripVersion(doc), acknowledged_floor_removals: ackFloor },
        rationale: draftReason.trim(),
      });
      setDraft(null);
      setDraftReason("");
      setAckFloor([]);
      invalidate("/api/changes");
      invalidate("/api/scoring-model");
      reloadChanges();
      refreshMe();
      toast("Sent for approval — the other leaders were notified, with warnings and reading material");
    } catch (e) {
      toast(e instanceof Error ? e.message : "That didn’t go through — try again.");
    } finally {
      setBusy(false);
    }
  };

  const tabDefs: { key: TabKey; label: string }[] = [
    { key: "how", label: "How it works" },
    { key: "score", label: "The score" },
    { key: "batch", label: "Batch quality" },
    { key: "critic", label: "Bias critic" },
    { key: "versions", label: `Versions · ${data.versions.length + pending.length}` },
  ];

  return (
    <div style={{ height: "100%", overflowY: "auto", padding: "28px 36px 40px" }}>
      <div style={{ maxWidth: 1160, margin: "0 auto" }} className="rise">
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 20, flexWrap: "wrap", marginBottom: 6 }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <h1 style={{ fontSize: 24, margin: 0, letterSpacing: "-0.01em" }}>Scoring model</h1>
              <select
                aria-label="Show the model as it applies in"
                value={lens ?? ""}
                onChange={(e) => setLens(e.target.value || null)}
                style={{ height: 32, border: "1px solid var(--border-strong)", borderRadius: 9, padding: "0 8px", fontSize: 12, fontWeight: 700, background: "var(--white)", color: "var(--ink)", fontFamily: "inherit" }}
              >
                <option value="">Global model</option>
                {(packs?.jurisdictions ?? []).map((j) => (
                  <option key={j.code} value={j.code}>As applied in {j.name}</option>
                ))}
              </select>
            </div>
            <div style={{ fontSize: 13, color: "var(--ink-soft)", lineHeight: 1.55, maxWidth: 620, marginTop: 6 }}>
              <strong style={{ color: "var(--ink)" }}>One scoring model for every location.</strong>{" "}
              A location&rsquo;s hiring-rules pack turns the dial — which AI signals fire there,
              how far each may move a score — so pick a location above to see the model as it
              applies there. Owned here, by policy, not by developers: change anything, and
              nothing takes effect until a second leader signs off.
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6 }}>
            <span style={{ display: "inline-flex", alignItems: "center", fontSize: 11.5, fontWeight: 700, padding: "4px 12px", borderRadius: 999, background: "var(--brand-green-tint)", color: "var(--brand-green-deep)", whiteSpace: "nowrap" }}>
              {lens && applied
                ? `v${data.version} · as applied in ${lensName} · ${applied.pack} pack v${applied.pack_version ?? "—"}`
                : `v${data.version} in effect · everywhere · since ${since}`}
            </span>
            {pending.length > 0 && (
              <button
                onClick={() => navigate("/approvals")}
                style={{ display: "inline-flex", alignItems: "center", fontSize: 11.5, fontWeight: 700, padding: "4px 12px", borderRadius: 999, background: "var(--surface-warn)", color: "var(--warn-ink)", cursor: "pointer", whiteSpace: "nowrap", border: 0, fontFamily: "inherit" }}
              >
                v{pending[0].base_version + 1} pending · awaiting a second leader
              </button>
            )}
          </div>
        </div>

        <div style={{ display: "flex", gap: 22, marginBottom: 18, fontSize: 13, fontWeight: 700, borderBottom: "1px solid var(--border-subtle)" }}>
          {tabDefs.map((t) => (
            <button
              key={t.key}
              role="tab"
              aria-selected={tab === t.key}
              onClick={() => navigate(`/scoring/${t.key}`, { replace: true })}
              style={{
                cursor: "pointer", paddingBottom: 9, border: 0, background: "none",
                fontFamily: "inherit", fontSize: 13, fontWeight: 700,
                color: tab === t.key ? "var(--brand-green-deep)" : "var(--ink-soft)",
                borderBottom: tab === t.key ? "2.5px solid var(--brand-green)" : "2.5px solid transparent",
                marginBottom: -1,
              }}
            >
              {t.label}
            </button>
          ))}
        </div>

        {lens && (
          <div style={{ fontSize: 13, color: "var(--ink-soft)", lineHeight: 1.55, marginBottom: 14 }}>
            Showing the model as it applies in <strong style={{ color: "var(--ink)" }}>{lensName}</strong> ·
            read-only. The bands, the evidence credits, how far all AI signals may move a score
            together, the preference cap and the confidence rules are the same in every location —
            only the signal switches, caps and market settings differ.{" "}
            <button
              type="button"
              className="link-quiet"
              onClick={() => navigate("/jurisdictions")}
            >
              Change {lensName}&rsquo;s settings
            </button>
          </div>
        )}
        {lensError && (
          <div role="alert" style={{ marginBottom: 16, padding: "12px 14px", borderRadius: 10, background: "var(--surface-warn)", color: "var(--warn-ink)", fontSize: 13 }}>
            {lensError}
          </div>
        )}

        {conflicts.length > 0 && (
          <div role="alert" style={{ marginBottom: 16, padding: "12px 14px", borderRadius: 10, background: "var(--surface-warn)", color: "var(--warn-ink)", fontSize: 13, lineHeight: 1.5 }}>
            <strong>A jurisdiction's floor changed under this model.</strong>{" "}
            {conflicts.length} scoring {conflicts.length > 1 ? "signals" : "signal"} now{" "}
            {conflicts.length > 1 ? "conflict" : "conflicts"} with a region's blocked factors and{" "}
            {conflicts.length > 1 ? "are" : "is"} stale. The jurisdiction floor overrides the model for new roles automatically — reconcile the model so it agrees:
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {conflicts.map((c, i) => (
                <li key={i}><b>{c.signal_name}</b> vs {c.jurisdiction} — {c.reason}</li>
              ))}
            </ul>
          </div>
        )}

        <div style={{ display: "flex", gap: 20, alignItems: "flex-start" }}>
          <div style={{ flex: "1 1 auto", minWidth: 0 }}>
            {(tab === "how" || tab === "score") && <FormulaStrip doc={doc} />}
            {tab === "how" && (
              <HowTab
                doc={doc}
                publishedParts={publishedPreview ? partsFromPreview(publishedPreview) : null}
                goSection={goSection}
              />
            )}
            {tab === "score" && (
              <ScoreTab
                doc={doc}
                published={published}
                knobs={data.knobs}
                stage={stage}
                cards={{ open: cardOpen, toggle: (k) => { setCardOpen((m) => ({ ...m, [k]: !m[k] })); setLastCard(k); }, flash }}
                medianMove={data.median_recent_move}
                tips={TIPS}
              />
            )}
            {tab === "batch" && (
              <BatchTab doc={doc} published={published} knobs={data.knobs} stage={stage} />
            )}
            {tab === "critic" && (
              <CriticTab
                doc={doc}
                published={published}
                stage={stage}
                cards={{ open: cardOpen, toggle: () => {}, flash }}
                onFloorRemovalAcknowledged={(id) => setAckFloor((a) => (a.includes(id) ? a : [...a, id]))}
                authorLabel={me?.user ?? "you"}
                tips={TIPS}
                goSection={goSection}
              />
            )}
            {tab === "versions" && (
              <VersionsTab
                versions={data.versions}
                currentVersion={data.version}
                pending={pending}
                onRestore={(restored, reason) => {
                  setDraft({ ...restored, version: published.version });
                  setDraftReason(reason);
                }}
              />
            )}
          </div>

          <Calculator
            doc={doc}
            published={published}
            dirty={dirty}
            preview={preview}
            publishedPreview={publishedPreview}
            dirtySections={dirtySections}
            highlight={calcHighlight}
            goSection={goSection}
            tips={TIPS}
          />
        </div>

        {dirty && !lens && (
          <DraftBar
            count={stagedCount}
            warnings={warnings}
            published={published}
            reason={draftReason}
            onReason={setDraftReason}
            onDiscard={() => { setDraft(null); setDraftReason(""); setAckFloor([]); }}
            onSubmit={submit}
            onRestoreTopic={(topicId) => stage((d) => {
              const original = published.topics.find((t) => t.id === topicId);
              if (original && !d.topics.some((t) => t.id === topicId)) {
                const index = published.topics.indexOf(original);
                d.topics.splice(Math.min(index, d.topics.length), 0, JSON.parse(JSON.stringify(original)));
              }
            })}
            goSection={goSection}
            busy={busy}
          />
        )}
      </div>
    </div>
  );
}
