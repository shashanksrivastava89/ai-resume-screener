import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { Requirement, RequirementKind } from "../types";
import { ImportanceMeter, InfoDot } from "./ui";
import { ConfirmModal } from "./overlays";
import { CATEGORIES, DEFAULT_TYPE_FOR, categoryOf } from "../categories";
import type { Category } from "../categories";
import { IconSkill, IconExperience, IconQualification, IconTrash, IconUndo, IconPlus } from "./icons";

// The plain-language rubric row editor shared by the new-requisition wizard and
// the workspace Checklist tab, transcribed from the v3 design prototype: one
// white card per requirement (text · Nice-to-have/Must-have/Hard gate segmented
// pill · importance), a gate explainer under gate rows, and the amber critic strip
// under flagged rows. Recruiter language only; weights map server-side.
//
// Toggling a row to Hard gate keeps its typed importance in state, so switching
// back restores it — the number is simply not rendered (or saved) for gates.
//
// GROUPED (2026-08-20). Rows are filed under Skills / Experience / Qualifications,
// derived from each row's `type` via ../categories (mirroring models.CATEGORY_OF_TYPE,
// which is the source of truth and sends a resolved `category` on every saved row).
// Three consequences worth knowing before editing this file:
//   1. The FLAT list stays the contract. Grouping is a view; publishing still sends
//      one ordered list, so the server and every rubric YAML are untouched.
//   2. Serial numbers are GLOBAL, not per group — recruiters quote "row 7" to each
//      other, so renderRow always receives the index into the flat list.
//   3. The single bottom add-row became one collapsed add-row PER group, because the
//      group is what supplies the new row's `type`. That is the whole reason a
//      recruiter never has to pick from the eight-value type enum.
// "Other" renders only when something lands in it: an unclassifiable requirement is
// never hidden (guardrail §6.3), but it never invents a fourth heading either.

// The three kinds, three colours (2026-09-06, pilot feedback item 10): blue =
// nice-to-have (adds to the score), green = must-have (the core — a gap caps the
// band), ink = hard gate (pass/fail, the heaviest). The row's left accent, the text
// box border and the active pill segment all carry the row's colour (app.css
// .kind-*), so a recruiter reads the kind before reading the word.
const KINDS: { value: RequirementKind; label: string; short: string }[] = [
  { value: "nice_to_have", label: "Nice-to-have", short: "Nice" },
  { value: "must_have", label: "Must-have", short: "Must" },
  { value: "hard_gate", label: "Hard gate", short: "Gate" },
];

// Said once, behind the gate slot's info dot — no longer a line under every gate
// row, which made the row grow and shrink as the kind was toggled.
const GATE_EXPLAINER = "Candidates without this stay listed but rank below everyone who has it — for true deal-breakers only.";

/** The colour key shown once above the rows. */
export function KindLegend() {
  return (
    <span className="kind-legend" aria-label="Colour key">
      {KINDS.map((k) => <span key={k.value} className={`kind-${k.value}`}><i aria-hidden="true" />{k.label}</span>)}
    </span>
  );
}

// A one-line textarea that grows with its content, so full-sentence requirements
// stay readable instead of scrolling inside a narrow input. Enter is swallowed —
// a requirement is one logical line (it feeds the rubric server-side); wrapping
// is visual only.
function GrowText({ value, placeholder, warned, onChange }: {
  value: string; placeholder: string; warned?: boolean; onChange: (v: string) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);
  return (
    <textarea
      ref={ref}
      rows={1}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => { if (e.key === "Enter") e.preventDefault(); }}
      className="req-text"
      style={{
        flex: "1 1 240px", minWidth: 240, fontSize: 13, lineHeight: 1.5,
        border: `1px solid ${warned ? "var(--accent-yellow)" : "var(--border-strong)"}`,
        borderRadius: 9, padding: "8px 11px", background: "var(--white)",
        color: "var(--ink)", fontFamily: "inherit", resize: "none", overflow: "hidden",
      }}
    />
  );
}

function KindControl({ value, onChange }: { value: RequirementKind; onChange: (k: RequirementKind) => void }) {
  return (
    <span role="group" aria-label="Requirement kind" className="kind-seg">
      {KINDS.map((k) => {
        const active = value === k.value;
        return (
          <button
            key={k.value}
            onClick={() => onChange(k.value)}
            aria-pressed={active}
            className={(active ? "is-on " : "") + `kind-${k.value}`}
            title={k.value === "hard_gate" ? "Pass/fail — a candidate without this ranks below everyone who has it"
              : k.value === "must_have" ? "Core requirement — missing it caps the band"
              : "Adds to the score when present; nothing lost without it"}
          >
            {k.label}
          </button>
        );
      })}
    </span>
  );
}

interface TrashEntry { row: Requirement; index: number; no: number; uid: number }
let trashUid = 0;

const GROUP_ICON: Record<Category, ReactNode> = {
  skills: <IconSkill size={14} />,
  experience: <IconExperience size={14} />,
  qualifications: <IconQualification size={14} />,
  other: null,
};
// One hue per group so the three sections read apart at a glance (2026-09-06).
const GROUP_HUE: Record<Category, string> = { skills: "teal", experience: "blue", qualifications: "violet", other: "slate" };

/** The add-row's dry-run critic verdict (POST …/checklist/check). */
export interface CheckVerdict { level: "ok" | "warn" | "blocked"; reason: string }

export default function ChecklistEditor({
  requirements,
  onChange,
  resetSignal,
  onCheckText,
  onAdded,
  renderBelowRow,
}: {
  requirements: Requirement[];
  onChange: (reqs: Requirement[]) => void;
  /** Change this to clear the pending-undo stack (a re-draft/reload supersedes
   *  the current rows, so an undo must never resurrect a superseded one). */
  resetSignal?: unknown;
  /** Dry-run critic for the add row ("Check & add"). Absent (the wizard, before
   *  the req exists) → rows add directly; publish still classifies server-side. */
  onCheckText?: (text: string) => Promise<CheckVerdict>;
  /** Toast hook — called with a plain sentence after an add. */
  onAdded?: (message: string) => void;
  /** Inline content under a specific row — the Checklist tab hangs each open HM
   *  suggestion directly below the requirement it edits (2026-07-19). */
  renderBelowRow?: (req: Requirement, index: number) => ReactNode;
}) {
  // The undo stack lives INSIDE the editor: it survives every parent re-render
  // (onChange swaps the requirements prop but never remounts this component) and
  // dies on unmount — session-only by construction. Capped so it can't grow
  // unbounded across a long editing session.
  const [trash, setTrash] = useState<TrashEntry[]>([]);
  const [newText, setNewText] = useState("");
  const [checking, setChecking] = useState(false);
  // `openAdd` is which group's add-row is currently expanded. One shared text buffer:
  // only one add-row can be open at a time, which keeps a half-typed requirement from
  // being forgotten under a collapsed heading.
  const [openAdd, setOpenAdd] = useState<Category | null>(null);
  const [confirm, setConfirm] = useState<{ verdict: CheckVerdict; text: string; cat: Category } | null>(null);
  useEffect(() => { setTrash([]); }, [resetSignal]);

  const update = (i: number, patch: Partial<Requirement>) =>
    onChange(requirements.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const remove = (i: number) => {
    setTrash((prev) => [{ row: requirements[i], index: i, no: displayNo.get(i) ?? i + 1, uid: trashUid++ }, ...prev].slice(0, 10));
    onChange(requirements.filter((_, j) => j !== i));
  };
  const undo = (uid: number) => {
    const entry = trash.find((t) => t.uid === uid);
    if (!entry) return;
    const at = Math.min(entry.index, requirements.length);
    onChange([...requirements.slice(0, at), entry.row, ...requirements.slice(at)]);
    setTrash((prev) => prev.filter((t) => t.uid !== uid));
  };
  const dismiss = (uid: number) => setTrash((prev) => prev.filter((t) => t.uid !== uid));

  // A new row is INSERTED after the last row of its own group rather than appended to
  // the very end, so it appears under the heading the recruiter typed beneath. Its
  // `type` comes from that group, which is how the category sticks without asking the
  // recruiter to pick one from a list of eight.
  const commitAdd = (text: string, cat: Category, verdict?: CheckVerdict) => {
    const flagged = verdict?.level === "warn";
    const row: Requirement = {
      text, kind: "must_have", importance: 3, type: DEFAULT_TYPE_FOR[cat],
      ...(flagged ? { critic_level: "warn" as const, critic_reason: verdict?.reason } : {}),
    };
    let at = requirements.length;
    for (let i = requirements.length - 1; i >= 0; i--) {
      if (categoryOf(requirements[i].type) === cat) { at = i + 1; break; }
    }
    onChange([...requirements.slice(0, at), row, ...requirements.slice(at)]);
    setNewText("");
    setConfirm(null);
    onAdded?.(flagged ? "Added — HR Head approval required at publish." : "Requirement added");
  };

  // The design's "Check & add": dry-run the same critic that gates publish.
  // blocked → an informational stop (reword or drop); warn → proceed knowingly
  // (the row carries the amber strip; publish routes through HR-Head approval).
  async function checkAndAdd(cat: Category) {
    const text = newText.trim();
    if (!text) return;
    if (!onCheckText) { commitAdd(text, cat); return; }
    setChecking(true);
    try {
      const verdict = await onCheckText(text);
      if (verdict.level === "ok") commitAdd(text, cat, verdict);
      else setConfirm({ verdict, text, cat });
    } catch {
      commitAdd(text, cat); // the check is best-effort — publish still enforces
    } finally {
      setChecking(false);
    }
  }

  // ONE row card. Rendered per group below. `i` is the GLOBAL index into the flat
  // list (update/remove address it); `no` is the DISPLAY number — the position on
  // screen, counted down the page across every group. Recruiters quote row numbers
  // to each other, so the number has to be the one they can see counting 1, 2, 3
  // down the page (2026-09-06: it used to be the stored order, which the grouping
  // scrambled into 1, 4, 2, 5, 3, 6).
  const renderRow = (req: Requirement, i: number, no: number) => {
    {
      const warned = req.critic_level === "warn";
      return (
          <div key={i} className={`req-card kind-${req.kind}`} style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 12, padding: "13px 16px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <span title={`Row ${no} of ${requirements.length}`} style={{ flex: "0 0 22px", fontSize: 13, fontWeight: 700, color: "var(--ink-muted)", fontVariantNumeric: "tabular-nums", textAlign: "right" }}>{no}</span>
              <GrowText
                value={req.text}
                warned={warned}
                placeholder="Describe the requirement in plain language…"
                onChange={(v) => update(i, { text: v })}
              />
              <KindControl value={req.kind} onChange={(kind) => update(i, { kind })} />
              {/* ONE fixed-width slot for both states (2026-09-06, item 1): the meter for
                  a weighted row, a same-sized "pass / fail" pill for a gate, with the
                  gate explainer behind its info dot — so flipping the kind never shifts
                  the delete button or grows the row. */}
              <span className="imp-slot">
                {req.kind === "hard_gate" ? (
                  <span className="imp-meter is-gate" role="group" aria-label="Hard gate: pass / fail">
                    <span className="imp-caption">pass / fail</span>
                    <span className="imp-label">Deal-breaker</span>
                    <InfoDot label="What a hard gate means" content={GATE_EXPLAINER} />
                  </span>
                ) : (
                  <ImportanceMeter value={req.importance} onChange={(v) => update(i, { importance: v })} />
                )}
              </span>
              <button
                onClick={() => remove(i)}
                aria-label="Remove requirement"
                title="Remove requirement"
                style={{ flex: "0 0 auto", display: "inline-flex", alignItems: "center", background: "none", border: 0, cursor: "pointer", color: "var(--ink-muted)", padding: 4 }}
              >
                <IconTrash size={15} />
              </button>
            </div>
            {warned && (
              <div style={{ display: "flex", gap: 8, fontSize: 12, lineHeight: 1.5, color: "var(--warn-ink)", background: "var(--surface-warn)", borderRadius: 9, padding: "8px 11px", marginTop: 10 }}>
                <span style={{ fontWeight: 700 }}>⚠</span>
                <span>{req.critic_reason} <strong>Needs HR Head approval at publish.</strong></span>
              </div>
            )}
            {renderBelowRow?.(req, i)}
          </div>
        );
      }
  };

  // Group the FLAT list by derived category, carrying each row's global index along.
  // The flat list stays the single source of order — grouping is a view over it, so
  // publishing still sends one ordered list and the server contract is untouched.
  // Display numbers run down the page, group after group.
  let running = 0;
  const grouped = CATEGORIES.map((spec) => ({
    spec,
    rows: requirements
      .map((req, i) => ({ req, i }))
      .filter(({ req }) => categoryOf(req.type) === spec.key)
      .map((row) => ({ ...row, no: ++running })),
  }))
    // "Other" appears only when something lands in it — never an empty fourth heading,
    // but never a hidden requirement either.
    .filter(({ spec, rows }) => spec.key !== "other" || rows.length > 0);
  const displayNo = new Map<number, number>();
  for (const g of grouped) for (const r of g.rows) displayNo.set(r.i, r.no);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      {grouped.map(({ spec, rows }) => (
        <section key={spec.key} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="section-head">
            {GROUP_ICON[spec.key] && (
              <span className={`avatar avatar-hue-${GROUP_HUE[spec.key]}`} aria-hidden="true" style={{ width: 22, height: 22, borderRadius: 6 }}>
                {GROUP_ICON[spec.key]}
              </span>
            )}
            <span>{spec.label}</span>
            {/* Labelled, not a bare numeral: beside rows that are themselves numbered,
                a lone "3" is ambiguous about what it counts — to a screen reader and
                to anyone scanning the column of serial numbers. */}
            <span
              aria-label={`${rows.length} ${rows.length === 1 ? "requirement" : "requirements"}`}
              style={{ fontWeight: 700, color: "var(--ink-muted)", letterSpacing: 0, textTransform: "none" }}
            >
              {rows.length === 1 ? "1 requirement" : `${rows.length} requirements`}
            </span>
          </div>

          {rows.length === 0 ? (
            <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.5, color: "var(--ink-soft)" }}>
              {spec.hint}
            </p>
          ) : (
            rows.map(({ req, i, no }) => renderRow(req, i, no))
          )}

          {/* Per-group add row: the group decides the new row's type, so the category
              sticks without making the recruiter choose from a list of eight. */}
          {openAdd === spec.key ? (
            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <input
                autoFocus
                value={newText}
                onChange={(e) => setNewText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); void checkAndAdd(spec.key); }
                  if (e.key === "Escape") { setOpenAdd(null); setNewText(""); }
                }}
                placeholder={spec.placeholder}
                aria-label={`Add a ${spec.label} requirement`}
                style={{ flex: "1 1 240px", minWidth: 0, height: 38, border: "1px solid var(--border-strong)", borderRadius: 10, padding: "0 12px", fontSize: 13, background: "var(--white)", color: "var(--ink)", fontFamily: "inherit" }}
              />
              <button
                onClick={() => void checkAndAdd(spec.key)}
                disabled={checking || !newText.trim()}
                style={{ height: 34, padding: "0 15px", borderRadius: 999, border: "1.5px solid var(--brand-green)", background: "var(--white)", color: "var(--brand-green-deep)", fontSize: 12, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap", fontFamily: "inherit", opacity: checking || !newText.trim() ? 0.6 : 1 }}
              >
                {onCheckText ? (checking ? "Checking…" : "Check & add") : "Add requirement"}
              </button>
              {/* "Discard", not "Cancel": the critic's confirm dialog already owns
                  "Cancel", and two identically-named buttons on screen at once is
                  ambiguous — for a screen reader most of all. */}
              <button
                onClick={() => { setOpenAdd(null); setNewText(""); }}
                style={{ background: "none", border: 0, cursor: "pointer", fontFamily: "inherit", fontSize: 12.5, color: "var(--ink-muted)" }}
              >
                Discard
              </button>
            </div>
          ) : (
            <button
              onClick={() => { setOpenAdd(spec.key); setNewText(""); }}
              style={{ alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: 6, background: "none", border: "1px dashed var(--border-strong)", borderRadius: 999, padding: "6px 13px", cursor: "pointer", fontFamily: "inherit", fontSize: 12, fontWeight: 700, color: "var(--ink-soft)" }}
            >
              <IconPlus size={13} />
              Add {spec.label.toLowerCase()} requirement
            </button>
          )}
        </section>
      ))}

      {/* Undo bars for accidentally deleted rows — session-only, newest first. */}
      {trash.map((t) => (
        <div key={t.uid} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 14px", background: "var(--neutral-100)", border: "1px solid var(--neutral-150)", borderRadius: 10 }}>
          <span style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>
            Row {t.no} removed{t.row.text.trim() ? ` — “${t.row.text.trim().slice(0, 48)}${t.row.text.trim().length > 48 ? "…" : ""}”` : ""}
          </span>
          <button onClick={() => undo(t.uid)} style={{ display: "inline-flex", alignItems: "center", gap: 5, background: "none", border: 0, cursor: "pointer", fontFamily: "inherit", fontSize: 12.5, fontWeight: 700, color: "var(--brand-green-deep)" }}>
            <IconUndo size={13} />Undo
          </button>
          <button onClick={() => dismiss(t.uid)} aria-label="Dismiss" style={{ marginLeft: "auto", background: "none", border: 0, cursor: "pointer", color: "var(--ink-muted)", fontSize: 13, padding: 2 }}>✕</button>
        </div>
      ))}

      {/* the design's critic stops: blocked = informational (no proceed); warn =
          proceed knowingly, approval notification promised at publish */}
      {confirm && (
        <ConfirmModal
          spec={confirm.verdict.level === "blocked" ? {
            // Stated, not negotiated (2026-09-07): the copy used to suggest rewording
            // it "without the protected attribute", which is a route around the rule.
            title: "This can't go on the checklist",
            text: confirm.text,
            reason: confirm.verdict.reason,
            question: "Nothing to change here — this can't be part of how candidates are scored.",
            canProceed: false,
          } : {
            title: "This needs HR Head approval",
            text: confirm.text,
            reason: confirm.verdict.reason,
            question: "Approval from the HR Head is required because of this. Do you want to proceed?",
            cta: "Proceed — needs approval",
            canProceed: true,
            onProceed: () => commitAdd(confirm.text, confirm.cat, confirm.verdict),
          }}
          onCancel={() => setConfirm(null)}
        />
      )}
    </div>
  );
}
