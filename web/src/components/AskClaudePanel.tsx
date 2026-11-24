// P44: the "Ask Claude" panel. Copies a SERVER-ASSEMBLED redacted context
// bundle (checklist, bands, gate results, evidence quotes) to the clipboard so
// the recruiter/HM can paste it into claude.ai today. Every copy is recorded in
// the audit log (the POST emits handoff_copied). The MCP connector — asking live
// with the user's own tokens — is Phase 2, gated behind the ask_claude_mcp flag
// (which stays off), so the UI contract won't change when it lands.
import { useState } from "react";
import { Button } from "./ui";
import { IconX, IconSparkle } from "./icons";
import { post } from "../api";
import { useFeatureFlag } from "../hooks";

const STARTERS = [
  "Why did #2 outrank #5?",
  "Summarize the gate misses — any borderline?",
  "Draft an HM update on the top 5",
];

export default function AskClaudePanel({
  reqId,
  blind,
  checklistVersion,
  onClose,
}: {
  reqId: string;
  blind: boolean;
  checklistVersion?: number | null;
  onClose: () => void;
}) {
  const [question, setQuestion] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mcpOn = useFeatureFlag("ask_claude_mcp") ?? false;

  async function copyContext() {
    setBusy(true);
    setError(null);
    try {
      const { text } = await post<{ text: string }>(`/api/roles/${reqId}/handoff`, { blind });
      const payload = question ? `${text}\n\n---\n${question}` : text;
      try {
        await navigator.clipboard?.writeText?.(payload);
      } catch {
        /* clipboard unavailable (headless / denied) — the POST still logged the copy */
      }
      setToast("Copied — paste it into a new claude.ai chat.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ position: "absolute", inset: 0, zIndex: 60 }}>
      <div onClick={onClose} style={{ position: "absolute", inset: 0, background: "rgba(34,34,34,0.28)" }} />
      <div
        className="rise"
        role="dialog"
        aria-label="Ask Claude"
        style={{ position: "absolute", top: 0, right: 0, bottom: 0, width: "min(460px, 90%)", background: "var(--white)", boxShadow: "-10px 0 34px rgba(34,34,34,.16)", overflowY: "auto", padding: "22px 26px 40px" }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <IconSparkle size={18} />
            <h2 style={{ fontSize: 19, margin: 0 }}>Ask Claude</h2>
          </div>
          <button onClick={onClose} aria-label="Close" style={{ width: 34, height: 34, borderRadius: "50%", border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink-soft)", cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
            <IconX size={16} />
          </button>
        </div>

        <p style={{ fontSize: 13.5, lineHeight: 1.6, color: "var(--ink-soft)", margin: "0 0 18px" }}>
          Copies a redacted context bundle — checklist{checklistVersion ? ` v${checklistVersion}` : ""}, bands, gate
          results, evidence quotes. No contact details; names withheld in blind mode. Copies are recorded in the audit log.
        </p>

        <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--ink-soft)", marginBottom: 8 }}>
          Starter questions
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 20 }}>
          {STARTERS.map((q) => (
            <button
              key={q}
              onClick={() => setQuestion((cur) => (cur === q ? null : q))}
              aria-pressed={question === q}
              className={"pill" + (question === q ? " active" : "")}
              style={{ textAlign: "left" }}
            >
              {q}
            </button>
          ))}
          <span style={{ fontSize: 12, color: "var(--ink-muted)" }}>
            Pick one to append to the copy, or copy the context on its own.
          </span>
        </div>

        {error && <div style={{ fontSize: 12.5, color: "var(--warn-ink)", marginBottom: 12 }}>{error}</div>}
        {toast && <div style={{ fontSize: 12.5, fontWeight: 700, color: "var(--brand-green-deep)", marginBottom: 12 }}>{toast}</div>}

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <Button onClick={copyContext} disabled={busy}><IconSparkle size={15} /> Copy context</Button>
          <a
            href="(internal design artifact, not published)"
            target="_blank"
            rel="noreferrer"
            style={{ display: "inline-flex", alignItems: "center", height: 40, padding: "0 16px", borderRadius: 10, border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink)", fontSize: 13, fontWeight: 700, textDecoration: "none" }}
          >
            Open claude.ai
          </a>
          {mcpOn && (
            <Button variant="secondary" disabled title="Coming soon">Ask live</Button>
          )}
        </div>

        <div style={{ marginTop: 22, paddingTop: 14, borderTop: "1px solid var(--neutral-150)", fontSize: 12, color: "var(--ink-muted)" }}>
          Phase 2: MCP connector — ask live with your own tokens.
        </div>
      </div>
    </div>
  );
}
