import { useState } from "react";
import { IconCheck, IconCopy, IconChevronDown, IconChevronRight } from "./icons";

// The job description as a reference the checklist editor can open — TAs read the
// JD to decide must-have vs hard-gate rather than trusting the model's parse.
//
// 2026-09-07: it moved from a 320px right rail to a full-width band ABOVE the
// requirements, COLLAPSED by default. The rail cost the form a third of the screen
// permanently to serve an occasional read, and long requirement text was wrapping
// into three lines because of it. Open it when you need it; the width goes back to
// the work the rest of the time. One click copies the whole JD — recruiters paste
// it into iCIMS notes and emails constantly.
export default function JdPane({ jd, loading = false, defaultOpen = false }: {
  jd: string | null; loading?: boolean; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [copied, setCopied] = useState<"idle" | "yes" | "no">("idle");

  async function copy() {
    if (!jd) return;
    try { await navigator.clipboard.writeText(jd); setCopied("yes"); } catch { setCopied("no"); }
    window.setTimeout(() => setCopied("idle"), 1600);
  }

  return (
    <section style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: open ? "10px 14px 6px" : "8px 14px" }}>
        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          style={{ display: "inline-flex", alignItems: "center", gap: 7, background: "none", border: 0, padding: 0, cursor: "pointer", fontFamily: "inherit", fontSize: 12.5, fontWeight: 700, color: "var(--ink)" }}
        >
          {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
          Job description
          {!open && <span style={{ fontWeight: 400, color: "var(--ink-muted)" }}>— the source these requirements came from</span>}
        </button>
        {open && jd && (
          <button
            onClick={copy}
            aria-label="Copy job description"
            title={copied === "yes" ? "Copied" : copied === "no" ? "Couldn't copy — select the text instead" : "Copy job description"}
            style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 5, height: 26, padding: "0 9px", borderRadius: 8, border: "1px solid var(--border-subtle)", background: "var(--white)", cursor: "pointer", color: copied === "yes" ? "var(--brand-green-deep)" : "var(--ink-soft)", fontFamily: "inherit", fontSize: 11.5, fontWeight: 700 }}
          >
            {copied === "yes" ? <IconCheck size={13} /> : <IconCopy size={13} />}
            {copied === "yes" ? "Copied" : "Copy"}
          </button>
        )}
      </div>
      {open && (
        <div style={{ padding: "0 14px 14px", maxHeight: 260, overflowY: "auto" }}>
          {loading ? (
            <p style={{ fontSize: 13, color: "var(--ink-soft)", margin: 0 }}>…</p>
          ) : jd ? (
            <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--ink-soft)", margin: 0, whiteSpace: "pre-wrap" }}>{jd}</p>
          ) : (
            <p style={{ fontSize: 12.5, color: "var(--ink-muted)", margin: 0 }}>Couldn't load the job description.</p>
          )}
        </div>
      )}
    </section>
  );
}
