// Checklist version history (2026-09-07, pilot feedback item 3).
//
// "v3 in force" used to be dead text in a caption. It is the most consequential
// fact on the page — every score on the Review tab was produced against SOME
// version — so it is now the thing you click to see the whole history.
//
// The list answers the question that decides whether a version can be left behind:
// WAS ANYONE SCREENED AGAINST IT? A version with runs is part of the record; one
// without was a draft that got superseded. Opening a version shows it frozen as it
// was published, and "Restore" loads those rows into the editor — publishing them
// creates the NEXT version, so history is only ever appended to.
import { useState } from "react";
import { OverlayRoot } from "./overlays";
import { Button, Spinner } from "./ui";
import { IconX, IconClock } from "./icons";
import { get } from "../api";
import { useFetch } from "../hooks";
import { Person, usePeople } from "../people";
import { dualStamp } from "../time";
import type { Requirement, RubricVersion } from "../types";

const KIND_LABEL: Record<string, string> = {
  nice_to_have: "Nice-to-have", must_have: "Must-have", hard_gate: "Hard gate",
};

export default function ChecklistVersions({ reqId, tz, onRestore, onClose }: {
  reqId: string;
  tz?: string;
  /** Load a past version's rows into the editor (publishing them makes the next
   *  version). Absent for a read-only viewer — the history still opens. */
  onRestore?: (rows: Requirement[], version: number) => void;
  onClose: () => void;
}) {
  const people = usePeople();
  const { data, loading } = useFetch<{ versions: RubricVersion[] }>(`/api/roles/${reqId}/rubric/versions`);
  const versions = data?.versions ?? [];
  const [openVersion, setOpenVersion] = useState<number | null>(null);
  const [rows, setRows] = useState<Requirement[] | null>(null);
  const [busy, setBusy] = useState(false);

  async function peek(version: number) {
    if (openVersion === version) { setOpenVersion(null); setRows(null); return; }
    setOpenVersion(version);
    setRows(null);
    setBusy(true);
    try {
      const r = await get<{ requirements: Requirement[] }>(`/api/roles/${reqId}/rubric/versions/${version}`);
      setRows(r.requirements ?? []);
    } catch {
      setRows([]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <OverlayRoot onDismiss={onClose} zIndex={70} align="top">
      <div role="dialog" aria-label="Checklist version history" className="rise"
           style={{ position: "relative", width: "min(720px, 94%)", maxHeight: "84vh", overflowY: "auto",
                    background: "var(--white)", border: "1px solid var(--border-subtle)",
                    borderRadius: 16, boxShadow: "var(--shadow-lg)", padding: "20px 24px" }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12, marginBottom: 14 }}>
          <div style={{ flex: "1 1 auto" }}>
            <div style={{ fontSize: 16, fontWeight: 700, letterSpacing: "-0.01em" }}>Checklist history</div>
            <div style={{ fontSize: 12.5, color: "var(--ink-soft)", marginTop: 3 }}>
              Every published version. Restoring one loads it into the editor — publishing
              makes it the next version, so nothing is overwritten.
            </div>
          </div>
          <button onClick={onClose} aria-label="Close" style={{ flex: "0 0 auto", width: 30, height: 30, borderRadius: "50%", border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink-soft)", cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
            <IconX size={14} />
          </button>
        </div>

        {loading && <div style={{ padding: "20px 0", textAlign: "center" }}><Spinner size={22} /></div>}
        {!loading && versions.length === 0 && (
          <div style={{ fontSize: 13, color: "var(--ink-soft)" }}>Nothing published yet.</div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {versions.map((v) => {
            const isOpen = openVersion === v.version;
            return (
              <div key={v.version} className={"ver-row" + (v.is_current ? " is-current" : "")}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                  <span className="ver-badge">v{v.version}</span>
                  <div style={{ flex: "1 1 240px", minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 13 }}>
                      {v.is_current && <span className="chip chip-green" style={{ height: 20 }}>In force</span>}
                      {/* The fact that decides whether a version can be walked away from. */}
                      {v.screening_runs > 0 ? (
                        <span className="chip chip-orange" style={{ height: 20 }}
                              title={v.last_screened_ts ? `Last used ${dualStamp(v.last_screened_ts, tz)}` : undefined}>
                          Screened against · {v.screening_runs} run{v.screening_runs === 1 ? "" : "s"}
                        </span>
                      ) : (
                        <span className="chip chip-slate" style={{ height: 20 }}>Never used to screen</span>
                      )}
                      <span style={{ color: "var(--ink-soft)" }}>
                        {v.requirement_count} requirement{v.requirement_count === 1 ? "" : "s"}
                        {v.gate_count > 0 && ` · ${v.gate_count} gate${v.gate_count === 1 ? "" : "s"}`}
                        {v.must_count > 0 && ` · ${v.must_count} must-have${v.must_count === 1 ? "" : "s"}`}
                      </span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11.5, color: "var(--ink-muted)", marginTop: 3 }}>
                      <IconClock size={11} />
                      {v.published_ts ? dualStamp(v.published_ts, tz) : "date unknown"}
                      {v.published_by && <>· <Person email={v.published_by} people={people} avatar={false} /></>}
                    </div>
                  </div>
                  <button onClick={() => void peek(v.version)} aria-expanded={isOpen}
                          className="link-quiet" style={{ flex: "0 0 auto", fontWeight: 700, color: "var(--brand-green-deep)" }}>
                    {isOpen ? "Hide" : "View"}
                  </button>
                  {!v.is_current && onRestore && (
                    <Button size="sm" variant="secondary"
                            onClick={async () => {
                              let use = rows;
                              if (openVersion !== v.version || !use) {
                                try {
                                  const r = await get<{ requirements: Requirement[] }>(`/api/roles/${reqId}/rubric/versions/${v.version}`);
                                  use = r.requirements ?? [];
                                } catch { return; }
                              }
                              onRestore(use, v.version);
                            }}>
                      Restore
                    </Button>
                  )}
                </div>
                {isOpen && (
                  <div style={{ marginTop: 10, borderTop: "1px solid var(--neutral-150)", paddingTop: 10 }}>
                    {busy && !rows && <Spinner size={18} />}
                    {rows && rows.length === 0 && (
                      <div style={{ fontSize: 12.5, color: "var(--ink-muted)" }}>This version couldn't be read.</div>
                    )}
                    {rows && rows.length > 0 && (
                      <ol style={{ margin: 0, paddingLeft: 20, display: "flex", flexDirection: "column", gap: 6 }}>
                        {rows.map((r, i) => (
                          <li key={i} style={{ fontSize: 12.5, lineHeight: 1.5 }}>
                            {r.text}
                            <span className={`ver-kind kind-${r.kind}`}>{KIND_LABEL[r.kind] ?? r.kind}</span>
                          </li>
                        ))}
                      </ol>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </OverlayRoot>
  );
}
