// The region-lens picker (P38), rendered on the user-chip area. A menu of
// "All regions" + the regions this user can access (me.regions.codes). The
// chosen lens is persisted (RegionLensProvider → localStorage) and sent as
// ?region= on the Requisitions fetch; the server independently enforces the
// user's grant, so this only narrows what they already see.
//
// HIDDEN entirely when the region framework is unseeded (me.regions.codes empty)
// — local dev is byte-identical to before enforcement existed.
import { useEffect, useRef, useState } from "react";
import { put } from "../api";
import { useMe, useMeRefresh } from "../MeContext";
import { useRegionLens } from "../RegionContext";
import { useToast } from "./overlays";
import RegionLensInfo from "./RegionLensExplainer";
import { IconGlobe, IconChevronDown, IconCheck } from "./icons";

export default function RegionPicker() {
  const me = useMe();
  const refreshMe = useMeRefresh();
  const toast = useToast();
  const { lens, setLens, home, homeSource, isDefault } = useRegionLens();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // The names come from the server (me.regions.names) so a region added there
  // reads properly here without this file being edited (2026-09-12) — the old
  // hard-coded four are gone.
  const names = me?.regions?.names ?? {};
  const label = (code: string) => names[code] ?? code;

  async function makeDefault(code: string | null) {
    setBusy(true);
    try {
      await put("/api/me/home-region", { region: code });
      await refreshMe();
      toast(code
        ? `${label(code)} is now your default — it's what you'll see when you open the app.`
        : "Your default region is cleared.");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Couldn't save that.");
    } finally {
      setBusy(false);
      setOpen(false);
    }
  }

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const codes = me?.regions?.codes ?? [];
  // Unseeded region framework → no picker (exact pre-enforcement behavior).
  if (codes.length === 0) return null;

  const options: { code: string | null; text: string }[] = [
    { code: null, text: "All regions" },
    ...codes.map((c) => ({ code: c, text: label(c) })),
  ];
  const current = lens ? label(lens) : "All regions";

  return (
    <div ref={ref} style={{ position: "relative", marginBottom: 10, display: "flex",
                            alignItems: "center", gap: 6 }}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label="Region lens"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          width: "100%",
          background: "var(--neutral-100)",
          border: "1px solid var(--border-subtle)",
          borderRadius: 8,
          padding: "7px 10px",
          font: "inherit",
          fontSize: 12.5,
          fontWeight: 700,
          color: "var(--ink)",
          cursor: "pointer",
        }}
      >
        <IconGlobe size={15} />
        <span style={{ flex: "1 1 auto", textAlign: "left", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {current}
        </span>
        <IconChevronDown size={14} />
      </button>
      {/* Outside the button: a switch that could plausibly change how candidates
          are scored deserves an answer where the switch is, not in a document. */}
      <RegionLensInfo />
      {open && (
        <div
          role="menu"
          style={{
            position: "absolute",
            bottom: "calc(100% + 6px)",
            left: 0,
            right: 0,
            zIndex: 50,
            background: "var(--white)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 10,
            boxShadow: "var(--shadow-md, 0 8px 28px rgba(34,34,34,.14))",
            padding: 4,
          }}
        >
          {options.map((opt) => {
            const active = (opt.code ?? null) === (lens ?? null);
            return (
              <button
                key={opt.code ?? "__all"}
                role="menuitem"
                onClick={() => {
                  setLens(opt.code);
                  setOpen(false);
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  width: "100%",
                  background: active ? "var(--brand-green-tint)" : "none",
                  border: 0,
                  borderRadius: 7,
                  padding: "8px 10px",
                  font: "inherit",
                  fontSize: 13,
                  fontWeight: active ? 700 : 500,
                  color: "var(--ink)",
                  cursor: "pointer",
                  textAlign: "left",
                }}
              >
                <span style={{ width: 14, display: "inline-flex", flex: "0 0 auto" }}>
                  {active && <IconCheck size={14} />}
                </span>
                <span style={{ flex: "1 1 auto" }}>{opt.text}</span>
                {opt.code !== null && opt.code === home && (
                  <span style={{ fontSize: 10.5, fontWeight: 700, color: "var(--ink-muted)" }}>
                    default
                  </span>
                )}
              </button>
            );
          })}

          <div style={{ borderTop: "1px solid var(--neutral-150)", marginTop: 4, paddingTop: 4 }}>
            {lens && lens !== home && (
              <button
                role="menuitem"
                disabled={busy}
                onClick={() => makeDefault(lens)}
                style={menuFooterStyle}
              >
                Make {label(lens)} my default
              </button>
            )}
            {homeSource === "chosen" && (
              <button
                role="menuitem"
                disabled={busy}
                onClick={() => makeDefault(null)}
                style={menuFooterStyle}
              >
                Clear my default
              </button>
            )}
            {home && isDefault && (
              <div style={{ padding: "6px 10px", fontSize: 11.5, color: "var(--ink-muted)", lineHeight: 1.4 }}>
                {homeSource === "country"
                  ? "Your default comes from your directory record."
                  : homeSource === "sole_grant"
                    ? "The only region you have access to."
                    : "Your chosen default."}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

const menuFooterStyle: React.CSSProperties = {
  display: "block", width: "100%", background: "none", border: 0, borderRadius: 7,
  padding: "8px 10px", font: "inherit", fontSize: 12.5, color: "var(--ink-soft)",
  cursor: "pointer", textAlign: "left",
};
