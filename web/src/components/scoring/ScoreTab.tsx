// "The score" tab (v3 design): the five section cards — Evidence credits,
// AI signals (+ add-signal form + the total-cap row), Preference nudges,
// Bands & bounds (+ the must-have cap chips), and — outside the formula —
// the Confidence read. Each card opens with "Review & edit"; edited values
// carry the design's green ring + "was X" marker.
import { useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import type { ModelSignal, ScoringDoc, ScoringKnob } from "../../types";
import { InfoDot, SegmentedPill, Switch, ValueStepper } from "../ui";
import { useToast } from "../overlays";
import { capLabel, fmtN, getPath, setPath, signalName } from "./model";

export interface CardState {
  open: Record<string, boolean>;
  toggle: (key: string) => void;
  flash: string | null;
}

const affect = (bg: string, fg: string): CSSProperties => ({
  display: "inline-flex", fontSize: 9.5, fontWeight: 700, letterSpacing: "0.03em",
  textTransform: "uppercase", padding: "2px 8px", borderRadius: 6, background: bg, color: fg,
});

function sectionCard(key: string, cards: CardState, bg: string, border: string, openBorder: string): CSSProperties {
  const open = !!cards.open[key];
  return {
    borderRadius: 14, padding: "16px 20px", marginBottom: 14,
    transition: "border-color .2s, box-shadow .2s",
    background: bg, border: `1.5px solid ${open ? openBorder : border}`,
    ...(open ? { boxShadow: "var(--shadow-sm)" } : {}),
    ...(cards.flash === key ? { animation: "brss-flash 1.4s cubic-bezier(0.2,0,0,1)" } : {}),
  };
}

function CardHeader({ title, chip, chipStyle, children, open, onToggle }: {
  title: ReactNode; chip: string; chipStyle: CSSProperties;
  children: ReactNode; open: boolean; onToggle: () => void;
}) {
  return (
    <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
      <div>
        <div style={{ fontSize: 13.5, fontWeight: 700, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {title} <span style={chipStyle}>{chip}</span>
        </div>
        <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 4, lineHeight: 1.55 }}>{children}</div>
      </div>
      <button onClick={onToggle} style={{ border: 0, background: "none", fontFamily: "inherit", fontSize: 12, fontWeight: 700, cursor: "pointer", color: "var(--brand-green-deep)", whiteSpace: "nowrap" }}>
        {open ? "Done editing" : "Review & edit"}
      </button>
    </div>
  );
}

/** One editable number row: label + sub left, − value ＋ right; edited rows get
 * the design's white wash + green outline; the value carries "was X".
 * Exported for the Batch-quality tab, which renders the same rows. */
export function KnobRow({ knob, doc, published, stage, fmt, step, min, max }: {
  knob: ScoringKnob; doc: ScoringDoc; published: ScoringDoc;
  stage: (mutate: (d: ScoringDoc) => void) => void;
  fmt: (v: number) => string; step: number; min: number; max: number;
}) {
  const current = getPath(doc, knob.path) as number;
  const pub = getPath(published, knob.path) as number;
  const changed = current !== pub;
  const setV = (v: number) => stage((d) => setPath(d, knob.path, Math.round(Math.max(min, Math.min(max, v)) * 100) / 100));
  return (
    <div
      style={{
        display: "flex", alignItems: "center", gap: 12, padding: "10px 8px",
        margin: "0 -8px", borderBottom: "1px solid var(--neutral-150)",
        flexWrap: "wrap", borderRadius: 10,
        ...(changed ? { background: "rgba(255,255,255,0.65)", outline: "2px solid var(--brand-green)", outlineOffset: -2 } : {}),
      }}
    >
      <div style={{ flex: "1 1 240px", minWidth: 200 }}>
        <div style={{ fontSize: 13, fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 6 }}>
          {knob.label}
          {knob.technical_term && <InfoDot content={knob.technical_term} />}
        </div>
        <div style={{ fontSize: 11.5, color: "var(--ink-soft)", marginTop: 2, lineHeight: 1.5 }}>{knob.help}</div>
      </div>
      <ValueStepper
        label={fmt(current)}
        was={changed ? `was ${fmt(pub)}` : null}
        onDec={() => setV(current - step)}
        onInc={() => setV(current + step)}
      />
    </div>
  );
}

interface ScoreTabProps {
  doc: ScoringDoc;
  published: ScoringDoc;
  knobs: ScoringKnob[];
  stage: (mutate: (d: ScoringDoc) => void) => void;
  cards: CardState;
  medianMove: number | null;
  tips: Record<string, string>;
}

// The design's per-knob stepper/format table.
const KNOB_UI: Record<string, { step: number; min: number; max: number; fmt: (v: number) => string }> = {
  "coverage.match_value.full": { step: 0.05, min: -1, max: 2, fmt: (v) => `× ${fmtN(v)}` },
  "coverage.match_value.partial": { step: 0.05, min: -1, max: 2, fmt: (v) => `× ${fmtN(v)}` },
  "coverage.match_value.none": { step: 0.05, min: -1, max: 2, fmt: (v) => `× ${fmtN(v)}` },
  "bands.strong_at": { step: 1, min: 0, max: 200, fmt: (v) => `≥ ${fmtN(v)}` },
  "bands.possible_at": { step: 1, min: 0, max: 200, fmt: (v) => `≥ ${fmtN(v)}` },
  "clamp.min": { step: 5, min: -100, max: 100, fmt: fmtN },
  "clamp.max": { step: 5, min: 0, max: 200, fmt: fmtN },
  "adjustments.total_cap_points": { step: 1, min: 1, max: 30, fmt: (v) => `± ${fmtN(v)}` },
  "preferences.per_preference_cap_points": { step: 1, min: 0, max: 10, fmt: (v) => `± ${fmtN(v)}` },
  "preferences.review_months": { step: 1, min: 1, max: 36, fmt: (v) => `${fmtN(v)} mo` },
  "confidence.high_requires_at_least": { step: 0.01, min: 0, max: 1, fmt: fmtN },
  "confidence.low_below": { step: 0.01, min: 0, max: 1, fmt: fmtN },
  "confidence.low_when_downgraded_findings_over": { step: 1, min: 0, max: 10, fmt: (v) => `> ${fmtN(v)}` },
};

function rowsFor(paths: string[], props: ScoreTabProps) {
  const byPath = new Map(props.knobs.map((k) => [k.path, k]));
  return paths.map((path) => {
    const knob = byPath.get(path);
    const ui = KNOB_UI[path];
    if (!knob || !ui) return null;
    return (
      <KnobRow key={path} knob={knob} doc={props.doc} published={props.published}
               stage={props.stage} fmt={ui.fmt} step={ui.step} min={ui.min} max={ui.max} />
    );
  });
}

function SignalRow({ signal, published, index, stage }: {
  signal: ModelSignal; published: ModelSignal | undefined; index: number;
  stage: (mutate: (d: ScoringDoc) => void) => void;
}) {
  const [whyOpen, setWhyOpen] = useState(false);
  const changed = !published
    || published.cap_points !== signal.cap_points
    || published.direction !== signal.direction
    || published.enabled_default !== signal.enabled_default;
  const capWas = published && (published.cap_points !== signal.cap_points || published.direction !== signal.direction)
    ? `was ${capLabel(published)}` : null;
  const mutate = (fn: (s: ModelSignal) => void) => stage((d) => fn(d.signals[index]));
  return (
    <div
      style={{
        display: "flex", alignItems: "flex-start", gap: 12, padding: "11px 12px",
        margin: "0 -12px", flexWrap: "wrap", borderBottom: "1px solid var(--neutral-150)",
        borderRadius: 10, opacity: signal.enabled_default ? 1 : 0.55,
        ...(changed ? { background: "rgba(255,255,255,0.65)", outline: "2px solid var(--brand-green)", outlineOffset: -2 } : {}),
      }}
    >
      <div style={{ flex: "1 1 260px", minWidth: 230 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 13, fontWeight: 700 }}>{signalName(signal)}</span>
          {!published && (
            <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 6, background: "var(--brand-green-tint)", color: "var(--brand-green-deep)" }}>
              new — off until approved
            </span>
          )}
          <button onClick={() => setWhyOpen((o) => !o)} style={{ border: 0, background: "none", fontFamily: "inherit", fontSize: 11, fontWeight: 700, cursor: "pointer", color: "var(--brand-green-deep)" }}>
            {whyOpen ? "hide" : "why job-related?"}
          </button>
        </div>
        <div style={{ fontSize: 11.5, color: "var(--ink-soft)", lineHeight: 1.5, marginTop: 2 }}>{signal.description}</div>
        {whyOpen && (
          <div style={{ fontSize: 11.5, color: "var(--ink)", background: "var(--neutral-100)", borderRadius: 8, padding: "7px 10px", marginTop: 6, lineHeight: 1.5 }}>
            <strong>Job-related because:</strong> {signal.job_relatedness}
          </div>
        )}
      </div>
      <SegmentedPill
        size="sm"
        options={[{ key: "plus", label: "+ only" }, { key: "both", label: "±" }]}
        value={signal.direction === "both" ? "both" : "plus"}
        onChange={(k) => mutate((s) => { s.direction = k as "plus" | "both"; })}
      />
      <span style={{ display: "inline-flex", alignItems: "center", gap: 5, flex: "0 0 auto" }}>
        <ValueStepper
          label={capLabel(signal)}
          was={capWas}
          minWidth={44}
          onDec={() => mutate((s) => { s.cap_points = Math.max(1, s.cap_points - 1); })}
          onInc={() => mutate((s) => { s.cap_points = Math.min(15, s.cap_points + 1); })}
        />
      </span>
      <span style={{ marginTop: 2 }}>
        <Switch checked={signal.enabled_default} onChange={(v) => mutate((s) => { s.enabled_default = v; })} />
      </span>
    </div>
  );
}

function AddSignalForm({ stage, onClose }: {
  stage: (mutate: (d: ScoringDoc) => void) => void; onClose: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [why, setWhy] = useState("");
  const [dir, setDir] = useState<"plus" | "both">("plus");
  const [cap, setCap] = useState(2);

  const save = () => {
    if (!name.trim() || !why.trim()) {
      toast("Name and job-relatedness are required — auditors read both");
      return;
    }
    const id = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || `factor_${Date.now()}`;
    stage((d) => {
      d.signals.push({
        id, display_name: name.trim(), description: desc.trim() || "—",
        definition: desc.trim() || name.trim(), kind: "judged", direction: dir,
        cap_points: cap, enabled_default: false, retired: false,
        job_relatedness: why.trim(), notes: null,
      });
    });
    onClose();
    toast("Staged — new signals start OFF and apply only after approval");
  };

  const input: CSSProperties = { width: "100%", height: 36, border: "1px solid var(--border-strong)", borderRadius: 9, padding: "0 11px", fontSize: 13, color: "var(--ink)", fontFamily: "inherit" };
  return (
    <div style={{ border: "1.5px solid var(--brand-green)", borderRadius: 12, padding: "13px 15px", marginTop: 10 }}>
      <div style={{ fontSize: 12.5, fontWeight: 700, marginBottom: 8 }}>New signal — starts OFF until approved</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name — e.g. Channel partner tenure" style={input} />
        <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="What it rewards, in plain words — recruiters read this verbatim" style={input} />
        <input value={why} onChange={(e) => setWhy(e.target.value)} placeholder="Why it’s job-related — required; auditors read this" style={input} />
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <SegmentedPill size="sm" options={[{ key: "plus", label: "+ only" }, { key: "both", label: "±" }]} value={dir} onChange={(k) => setDir(k as "plus" | "both")} />
          <ValueStepper
            label={`${dir === "both" ? "±" : "+"}${cap}`}
            minWidth={44}
            onDec={() => setCap((c) => Math.max(1, c - 1))}
            onInc={() => setCap((c) => Math.min(15, c + 1))}
          />
          <button onClick={save} style={{ height: 32, padding: "0 15px", borderRadius: 999, border: 0, background: "var(--brand-green)", color: "#fff", fontFamily: "inherit", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
            Stage new signal
          </button>
          <button onClick={onClose} style={{ border: 0, background: "none", fontFamily: "inherit", fontSize: 12, fontWeight: 700, cursor: "pointer", color: "var(--ink-soft)" }}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

export default function ScoreTab(props: ScoreTabProps) {
  const { doc, published, stage, cards, medianMove, tips } = props;
  const [addOpen, setAddOpen] = useState(false);
  const publishedById = new Map(published.signals.map((s) => [s.id, s]));

  const upCap = doc.signals.filter((g) => g.enabled_default && !g.retired)
    .reduce((t, g) => t + g.cap_points, 0);
  const downCap = doc.signals.filter((g) => g.enabled_default && !g.retired && g.direction !== "plus")
    .reduce((t, g) => t + g.cap_points, 0);
  const budget = `All caps together: up to +${fmtN(upCap)} / −${fmtN(downCap)} vs a base of 100`
    + ` · held to ±${fmtN(doc.adjustments.total_cap_points)} all together`
    + (medianMove != null ? ` · median real move last quarter: ${medianMove >= 0 ? "+" : "−"}${fmtN(Math.abs(medianMove))}` : "");

  const mustCap = doc.bands.cap_if_must_have_missing;
  const mustCapChanged = mustCap !== published.bands.cap_if_must_have_missing;

  return (
    <>
      {/* ── Evidence credits ─────────────────────────────────────────────── */}
      <div id="sec-credits" style={sectionCard("credits", cards, "var(--brand-green-tint)", "rgba(0,166,81,0.28)", "var(--brand-green)")}>
        <CardHeader
          title="Evidence credits"
          chip="changes: checklist base — the no-AI math"
          chipStyle={affect("var(--brand-green-tint)", "var(--brand-green-deep)")}
          open={!!cards.open.credits}
          onToggle={() => cards.toggle("credits")}
        >
          The checklist is the job’s requirement list a recruiter publishes per requisition — you’ll find it in any requisition’s workspace; Legal never edits it, but these credits price its evidence. Clear evidence pays a line’s full weight <InfoDot content={tips.weights} />, partial pays half, none pays nothing — summed into the base, out of 100. No AI anywhere in this part.
        </CardHeader>
        {cards.open.credits && (
          <div style={{ border: "1.5px dashed var(--border-strong)", borderRadius: 10, padding: 13, marginTop: 10, textAlign: "center", fontSize: 11.5, color: "var(--ink-muted)", background: "var(--neutral-100)", lineHeight: 1.5 }}>
            GIF placeholder — 8s loop: dragging “Some evidence” from ×0.5 to ×0.7 and watching the sample base climb in the calculator on the right
          </div>
        )}
        {cards.open.credits && rowsFor(["coverage.match_value.full", "coverage.match_value.partial", "coverage.match_value.none"], props)}
      </div>

      {/* ── AI signals ───────────────────────────────────────────────────── */}
      <div id="sec-signals" style={sectionCard("signals", cards, "var(--accent-blue-tint)", "rgba(45,140,196,0.3)", "var(--accent-blue)")}>
        <CardHeader
          title="AI signals — the AI-judged extras"
          chip="changes: AI signals — the AI part"
          chipStyle={affect("var(--accent-blue-tint)", "#16729E")}
          open={!!cards.open.signals}
          onToggle={() => cards.toggle("signals")}
        >
          Each signal is one thing the AI model may reward or subtract — always with a résumé quote, a hard cap <InfoDot content={tips.cap} /> and a direction <InfoDot content={tips.direction} /> you set here. This is where you calibrate how much AI judgment can move a score. Turning one off retires it from future runs — history is never rewritten.
          <div style={{ marginTop: 2 }}><span style={{ fontSize: 11.5, color: "var(--ink-soft)" }}>{budget}</span></div>
        </CardHeader>
        {cards.open.signals && doc.signals.map((g, i) => (
          <SignalRow key={g.id} signal={g} published={publishedById.get(g.id)} index={i} stage={stage} />
        ))}
        {cards.open.signals && rowsFor(["adjustments.total_cap_points"], props)}
        {cards.open.signals && (addOpen
          ? <AddSignalForm stage={stage} onClose={() => setAddOpen(false)} />
          : (
            <button onClick={() => setAddOpen(true)} style={{ marginTop: 10, height: 34, padding: "0 16px", borderRadius: 999, border: "1.5px solid var(--brand-green)", background: "var(--white)", color: "var(--brand-green-deep)", fontFamily: "inherit", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
              ＋ Add a signal
            </button>
          ))}
      </div>

      {/* ── Preference nudges ────────────────────────────────────────────── */}
      <div id="sec-prefs" style={sectionCard("prefs", cards, "var(--surface-warn)", "rgba(226,168,43,0.4)", "var(--accent-yellow)")}>
        <CardHeader
          title={<>Preference nudges <InfoDot content={tips.prefCap} /></>}
          chip="changes: preference nudges only"
          chipStyle={affect("var(--surface-warn)", "var(--warn-ink)")}
          open={!!cards.open.prefs}
          onToggle={() => cards.toggle("prefs")}
        >
          A preference is one recruiter’s personal nudge — “notice period ≤ 30 days”. It never overrides the checklist, hard gates or banned topics; every preference needs an HR Head’s sign-off before it counts (never the author’s own), and protected-attribute preferences are blocked outright — no approval path. Here you set how far one preference can sway any score, and how often approvals get re-checked.
        </CardHeader>
        {cards.open.prefs && rowsFor(["preferences.per_preference_cap_points", "preferences.review_months"], props)}
      </div>

      {/* ── Bands & bounds ───────────────────────────────────────────────── */}
      <div id="sec-bands" style={sectionCard("bands", cards, "var(--neutral-100)", "var(--border-strong)", "var(--ink-soft)")}>
        <CardHeader
          title="Bands & bounds"
          chip="changes: labels & limits — not base, not AI"
          chipStyle={affect("var(--neutral-150)", "#5A5A5A")}
          open={!!cards.open.bands}
          onToggle={() => cards.toggle("bands")}
        >
          The last step of the sequence — after base, signals and preferences are summed. Doesn’t change any number the formula produces and doesn’t alter how the AI judges — only where the labels Strong / Possible / Weak sit on the scale, and the floor/ceiling fence <InfoDot content={tips.clamp} /> the final number can never leave.
        </CardHeader>
        {cards.open.bands && rowsFor(["bands.strong_at", "bands.possible_at", "clamp.min", "clamp.max"], props)}
        {cards.open.bands && (
          <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 0 2px", flexWrap: "wrap" }}>
            <div style={{ flex: "1 1 240px", minWidth: 200 }}>
              <div style={{ fontSize: 13, fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 6 }}>
                Missing a must-have caps the label at
                {mustCapChanged && (
                  <span style={{ fontSize: 9, fontWeight: 700, color: "var(--brand-green-deep)" }}>
                    was {published.bands.cap_if_must_have_missing}
                  </span>
                )}
              </div>
              <div style={{ fontSize: 11.5, color: "var(--ink-soft)", marginTop: 2, lineHeight: 1.5 }}>
                A great nice-to-have story can’t paper over a missing core requirement.
              </div>
            </div>
            <SegmentedPill
              dark
              options={[{ key: "Strong", label: "Strong" }, { key: "Possible", label: "Possible" }, { key: "Not a fit", label: "Weak" }]}
              value={mustCap}
              onChange={(k) => stage((d) => { d.bands.cap_if_must_have_missing = k; })}
            />
          </div>
        )}
      </div>

      {/* ── outside the formula ──────────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "20px 0 12px" }}>
        <span style={{ flex: "1 1 auto", height: 1, background: "var(--border-strong)" }} />
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.05em", textTransform: "uppercase", color: "var(--ink-muted)" }}>
          Outside the formula — never changes a score
        </span>
        <span style={{ flex: "1 1 auto", height: 1, background: "var(--border-strong)" }} />
      </div>

      {/* ── Confidence read ──────────────────────────────────────────────── */}
      <div
        id="sec-conf"
        style={{
          borderRadius: 14, padding: "16px 20px", marginBottom: 14,
          background: "var(--white)", border: "1.5px dashed var(--border-strong)",
          ...(cards.flash === "conf" ? { animation: "brss-flash 1.4s cubic-bezier(0.2,0,0,1)" } : {}),
        }}
      >
        <CardHeader
          title={<>Confidence read <InfoDot content={tips.confidence} /></>}
          chip="changes: trust display — never the score"
          chipStyle={affect("var(--neutral-150)", "#5A5A5A")}
          open={!!cards.open.conf}
          onToggle={() => cards.toggle("conf")}
        >
          Every candidate row in Review carries a high / medium / low trust read — the system’s confidence in how well the résumé itself could be read, not part of the scoring equation. It is not an AI opinion — it’s measured: how cleanly the résumé text was extracted (scan quality), and how many of the AI’s claims the evidence-check rejected. It never changes any score — only how loudly a row says “double-check the résumé yourself”.
        </CardHeader>
        {cards.open.conf && rowsFor(["confidence.high_requires_at_least", "confidence.low_below", "confidence.low_when_downgraded_findings_over"], props)}
      </div>
    </>
  );
}
