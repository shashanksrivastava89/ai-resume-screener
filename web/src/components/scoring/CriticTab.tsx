// "Bias critic" tab (v3 design): what the critic is (intro + verdict chips),
// the never-scored topics — each with its reason, policy-floor marker,
// per-stage enforcement chips and the editable trigger-phrase list — and the
// add-topic form. Floor-topic removal runs through the exposure-acknowledgment
// confirm; everything lands on the same staged draft as The score tab.
import { useState } from "react";
import type { CSSProperties } from "react";
import type { ModelTopic, ScoringDoc, TopicStage } from "../../types";
import { Tip } from "../ui";
import { ConfirmModal, useToast, type ConfirmSpec } from "../overlays";
import { PHRASE_SUGGESTIONS, STAGE_ORDER, STAGE_TIPS } from "./model";
import type { CardState } from "./ScoreTab";

interface CriticTabProps {
  doc: ScoringDoc;
  published: ScoringDoc;
  stage: (mutate: (d: ScoringDoc) => void) => void;
  cards: CardState;
  onFloorRemovalAcknowledged: (topicId: string) => void;
  authorLabel: string;
  tips: Record<string, string>;
  goSection: (tab: string, card: string | null) => void;
}

const stageLabel = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export default function CriticTab({
  doc, published, stage, cards, onFloorRemovalAcknowledged, authorLabel, tips, goSection,
}: CriticTabProps) {
  const toast = useToast();
  const [phrasesOpen, setPhrasesOpen] = useState<string | null>(null);
  const [phraseInputs, setPhraseInputs] = useState<Record<string, string>>({});
  const [addText, setAddText] = useState("");
  const [addWhy, setAddWhy] = useState("");
  const [confirm, setConfirm] = useState<ConfirmSpec | null>(null);
  const publishedById = new Map(published.topics.map((t) => [t.id, t]));

  const removeTopic = (topic: ModelTopic) => {
    const doRemove = () => stage((d) => { d.topics = d.topics.filter((x) => x.id !== topic.id); });
    if (topic.floor) {
      setConfirm({
        title: "Remove a policy-floor protection?",
        text: topic.name,
        reason: "This topic is treated as law-adjacent (DPDP · GDPR · Title VII reference set). Removing it exposes Acme to discrimination claims — allowed, but the approver must acknowledge that exposure by name before it takes effect.",
        question: "Stage the removal? It enters the same approval flow as every other scoring change.",
        cta: "Stage removal",
        onProceed: () => {
          doRemove();
          onFloorRemovalAcknowledged(topic.id);
          setConfirm(null);
          toast("Removal staged — the approver must acknowledge the exposure");
        },
      });
    } else {
      doRemove();
      toast("Removal staged — takes effect only after approval");
    }
  };

  const chip = (bg: string, fg: string): CSSProperties => ({
    display: "inline-flex", fontSize: 11, fontWeight: 700, padding: "3px 10px",
    borderRadius: 999, background: bg, color: fg,
  });

  return (
    <>
      {confirm && <ConfirmModal spec={confirm} onCancel={() => setConfirm(null)} />}

      <div
        id="sec-criticIntro"
        style={{
          background: "var(--white)", border: "1px solid var(--border-subtle)",
          borderRadius: 14, padding: "16px 20px", marginBottom: 14,
          ...(cards.flash === "criticIntro" ? { animation: "brss-flash 1.4s cubic-bezier(0.2,0,0,1)" } : {}),
        }}
      >
        <div style={{ fontSize: 13, lineHeight: 1.6 }}>
          During every screening run, an <strong>AI model</strong> proposes the signal adjustments —{" "}
          <button onClick={() => goSection("score", "signals")} style={{ border: 0, background: "none", padding: 0, fontFamily: "inherit", fontSize: 13, fontWeight: 700, cursor: "pointer", color: "var(--brand-green-deep)" }}>
            the capped nudges from The score tab ›
          </button>{" "}
          — each backed by a résumé quote. The <strong>bias critic is part of the system, not a person</strong>: it runs automatically after the AI proposes and before anything counts, auditing the AI’s written reasoning for every adjustment. The <strong>candidate’s identity</strong> (name, age, address, photos, contact) was stripped before scoring, so demographic-shaped reasoning is a leak or an inference — both get rejected. <strong>Your role here:</strong> you decide what’s banned below and where it’s enforced; the critic applies your list to every run, automatically.
        </div>
        <div style={{ display: "flex", gap: 7, marginTop: 10, flexWrap: "wrap" }}>
          <span style={chip("var(--brand-green-tint)", "var(--brand-green-deep)")}>pass — evidence supports it</span>
          <span style={chip("var(--surface-warn)", "var(--warn-ink)")}>flag — counts, but a human looks</span>
          <span style={{ ...chip("var(--surface-inverted)", "#fff"), gap: 5, alignItems: "center" }}>
            suspend — rejected by code, kept visible
            <Tip content={tips.suspend}>
              <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 13, height: 13, borderRadius: "50%", border: "1px solid rgba(255,255,255,0.5)", fontSize: 9, cursor: "help" }}>i</span>
            </Tip>
          </span>
        </div>
        <div style={{ fontSize: 11.5, color: "var(--ink-soft)", marginTop: 9, lineHeight: 1.55 }}>
          Two independent checks: an exact-phrase code screen (editable below) and a reviewing AI model that catches paraphrases the phrases miss. When they disagree, the stricter verdict wins.
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 8 }}>
        <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--ink-soft)" }}>
          Never-scored topics · {doc.topics.length}
        </span>
        <span style={{ fontSize: 11.5, color: "var(--ink-muted)" }}>
          Every edit here changes scores — needs a second leader’s approval
        </span>
      </div>

      {doc.topics.map((topic) => {
        const pub = publishedById.get(topic.id);
        const pubPhrases = pub?.phrases ?? [];
        const phrasesChanged = !!pub && pubPhrases.join("") !== topic.phrases.join("");
        const removedPhrases = pubPhrases.filter((p) => !topic.phrases.includes(p));
        const open = phrasesOpen === topic.id;
        const mutate = (fn: (t: ModelTopic) => void) =>
          stage((d) => { const t = d.topics.find((x) => x.id === topic.id); if (t) fn(t); });
        return (
          <div
            key={topic.id}
            style={{
              background: "var(--white)",
              border: `1px solid ${pub ? "var(--border-subtle)" : "var(--brand-green)"}`,
              borderRadius: 12, padding: "12px 16px", marginBottom: 9,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontSize: 13, fontWeight: 700 }}>{topic.name}</span>
              {topic.floor && (
                <Tip content={tips.floor}>
                  <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", padding: "2px 8px", borderRadius: 6, background: "var(--surface-inverted)", color: "#fff", cursor: "help" }}>
                    Policy floor
                  </span>
                </Tip>
              )}
              {(topic.added_by || !pub) && (
                <span style={{ fontSize: 11, color: "var(--ink-muted)" }}>
                  {pub ? `Added by ${topic.added_by}` : `Staged by ${authorLabel}`}
                </span>
              )}
              <button
                onClick={() => removeTopic(topic)}
                title="Remove topic"
                aria-label={`Remove ${topic.name}`}
                style={{ marginLeft: "auto", border: 0, background: "none", cursor: "pointer", color: "var(--ink-muted)", fontSize: 14, fontFamily: "inherit" }}
              >
                ✕
              </button>
            </div>
            <div style={{ fontSize: 12, color: "var(--ink-soft)", lineHeight: 1.55, margin: "4px 0 8px" }}>{topic.reason}</div>
            <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
              <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--ink-muted)" }}>
                Enforced at · hover each
              </span>
              {STAGE_ORDER.map((sg) => {
                const on = topic.stages.includes(sg);
                const edited = !!pub && pub.stages.includes(sg) !== on;
                return (
                  <Tip key={sg} content={STAGE_TIPS[sg]}>
                    <button
                      onClick={() => mutate((t) => {
                        t.stages = on ? t.stages.filter((x) => x !== sg) : [...t.stages, sg] as TopicStage[];
                      })}
                      style={{
                        display: "inline-flex", fontSize: 10.5, fontWeight: 700, padding: "3px 9px",
                        borderRadius: 999, cursor: "pointer", fontFamily: "inherit",
                        border: `1px solid ${on ? "var(--brand-green)" : "var(--border-subtle)"}`,
                        background: on ? "var(--brand-green-tint)" : "var(--white)",
                        color: on ? "var(--brand-green-deep)" : "var(--ink-muted)",
                        ...(edited ? { boxShadow: "0 0 0 2px var(--brand-green)" } : {}),
                      }}
                    >
                      {stageLabel(sg)}
                    </button>
                  </Tip>
                );
              })}
              <button
                onClick={() => setPhrasesOpen(open ? null : topic.id)}
                style={{ marginLeft: "auto", border: 0, background: "none", fontFamily: "inherit", fontSize: 11.5, fontWeight: 700, cursor: "pointer", color: "var(--brand-green-deep)" }}
              >
                Trigger phrases · {topic.phrases.length}{phrasesChanged ? " — edited" : ""}
              </button>
            </div>
            {open && (
              <div style={{ marginTop: 9, padding: "10px 12px", background: "var(--neutral-100)", borderRadius: 9 }}>
                <div style={{ fontSize: 11, color: "var(--ink-soft)", marginBottom: 7, lineHeight: 1.5 }}>
                  Exact text matches — not meaning-based. Narrow on purpose: “gap analysis” is normal finance vocabulary and must never trigger. You don’t need every wording — a separate reviewing AI model catches paraphrases; these phrases are the guaranteed, code-level floor. Ask the AI to suggest more.
                </div>
                <div style={{ display: "flex", gap: 5, flexWrap: "wrap", alignItems: "center" }}>
                  {topic.phrases.map((ph) => (
                    <span
                      key={ph}
                      style={{
                        display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5,
                        fontFamily: "monospace", padding: "3px 8px", borderRadius: 6,
                        ...(pub && !pubPhrases.includes(ph)
                          ? { background: "var(--brand-green-tint)", border: "1.5px solid var(--brand-green)" }
                          : { background: "var(--white)", border: "1px solid var(--border-subtle)" }),
                      }}
                    >
                      “{ph}”
                      <button
                        onClick={() => mutate((t) => { t.phrases = t.phrases.filter((x) => x !== ph); })}
                        title="Remove phrase"
                        style={{ border: 0, background: "none", cursor: "pointer", color: "var(--ink-muted)", fontFamily: "inherit", padding: 0 }}
                      >
                        ✕
                      </button>
                    </span>
                  ))}
                  {removedPhrases.map((ph) => (
                    <span key={ph} style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, fontFamily: "monospace", padding: "3px 8px", borderRadius: 6, background: "var(--neutral-100)", border: "1px dashed var(--border-strong)", color: "var(--ink-muted)", textDecoration: "line-through" }}>
                      “{ph}”
                      <button
                        onClick={() => mutate((t) => { t.phrases = [...t.phrases, ph]; })}
                        title="Restore phrase"
                        style={{ border: 0, background: "none", cursor: "pointer", color: "var(--ink-muted)", fontFamily: "inherit", padding: 0, textDecoration: "none" }}
                      >
                        ↺
                      </button>
                    </span>
                  ))}
                  <input
                    value={phraseInputs[topic.id] ?? ""}
                    onChange={(e) => setPhraseInputs((m) => ({ ...m, [topic.id]: e.target.value }))}
                    placeholder="add phrase…"
                    style={{ height: 26, width: 130, border: "1px solid var(--border-strong)", borderRadius: 6, padding: "0 8px", fontSize: 11.5, fontFamily: "monospace", color: "var(--ink)", background: "var(--white)" }}
                  />
                  <button
                    onClick={() => {
                      const v = (phraseInputs[topic.id] ?? "").trim();
                      if (!v) return;
                      mutate((t) => { if (!t.phrases.includes(v)) t.phrases = [...t.phrases, v]; });
                      setPhraseInputs((m) => ({ ...m, [topic.id]: "" }));
                    }}
                    style={{ height: 26, padding: "0 10px", borderRadius: 999, border: "1.5px solid var(--brand-green)", background: "var(--white)", color: "var(--brand-green-deep)", fontFamily: "inherit", fontSize: 11, fontWeight: 700, cursor: "pointer" }}
                  >
                    Add
                  </button>
                  <button
                    onClick={() => {
                      const suggestions = (PHRASE_SUGGESTIONS[topic.id] || []).filter((p) => !topic.phrases.includes(p));
                      if (!suggestions.length) {
                        toast("No new suggestions — the AI reads this list as covered");
                        return;
                      }
                      mutate((t) => { t.phrases = [...t.phrases, ...suggestions]; });
                      toast(`${suggestions.length} AI-suggested phrase(s) staged — remove any; approval still applies`);
                    }}
                    style={{ height: 26, padding: "0 10px", borderRadius: 999, border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink-soft)", fontFamily: "inherit", fontSize: 11, fontWeight: 700, cursor: "pointer" }}
                  >
                    ✨ AI suggest
                  </button>
                  {phrasesChanged && (
                    <button
                      onClick={() => mutate((t) => { t.phrases = [...pubPhrases]; })}
                      title="Undo staged phrase edits — back to the published list"
                      style={{ border: 0, background: "none", fontFamily: "inherit", fontSize: 11, fontWeight: 700, cursor: "pointer", color: "var(--ink-soft)", marginLeft: "auto", paddingLeft: 22, whiteSpace: "nowrap" }}
                    >
                      ↺ Reset
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })}

      <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 10, flexWrap: "wrap" }}>
        <input
          value={addText}
          onChange={(e) => setAddText(e.target.value)}
          placeholder="Ban another topic — e.g. Visa or work-permit status…"
          style={{ flex: "1 1 220px", height: 38, border: "1px solid var(--border-strong)", borderRadius: 10, padding: "0 12px", fontSize: 13, background: "var(--white)", color: "var(--ink)", fontFamily: "inherit" }}
        />
        <input
          value={addWhy}
          onChange={(e) => setAddWhy(e.target.value)}
          placeholder="Why it’s never scored — required; travels with this topic…"
          style={{ flex: "1 1 280px", height: 38, border: "1px solid var(--border-strong)", borderRadius: 10, padding: "0 12px", fontSize: 13, background: "var(--white)", color: "var(--ink)", fontFamily: "inherit" }}
        />
        <button
          onClick={() => {
            const name = addText.trim();
            const why = addWhy.trim();
            if (!name) return;
            if (!why) {
              toast("Add the reasoning — it travels with this topic; the approver and auditors read it verbatim");
              return;
            }
            const id = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || `topic_${Date.now()}`;
            stage((d) => {
              d.topics.push({
                id, name, reason: why, floor: false, added_by: authorLabel,
                added_at: null, stages: ["scoring", "checklist", "preferences"], phrases: [],
              });
            });
            setAddText("");
            setAddWhy("");
            toast("Staged with your reasoning — enforcement begins only after approval");
          }}
          style={{ height: 34, padding: "0 15px", borderRadius: 999, border: "1.5px solid var(--brand-green)", background: "var(--white)", color: "var(--brand-green-deep)", fontFamily: "inherit", fontSize: 12, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap" }}
        >
          Stage topic
        </button>
      </div>
    </>
  );
}
