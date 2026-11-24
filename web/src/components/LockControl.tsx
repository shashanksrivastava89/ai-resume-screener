// A manual lock/unlock control (2026-07-20) for a requisition's checklist or
// candidate list. Locking freezes edits; unlock is offered only to the locker,
// an HR Head or a Super Admin (reqs_all) — matching the server, which enforces it.
import { useState } from "react";
import { post } from "../api";
import { useMe } from "../MeContext";
import { humanizeEmail } from "../names";
import type { LockInfo } from "../types";

export default function LockControl({ reqId, target, lock, label, onChange }: {
  reqId: string;
  target: "checklist" | "candidates";
  lock: LockInfo | null | undefined;
  label: string; // "Checklist" | "Candidates"
  onChange: () => void;
}) {
  const me = useMe();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const caps = me?.capabilities ?? [];
  const canUnlock = !!lock
    && (lock.locked_by.toLowerCase() === (me?.user ?? "").toLowerCase() || caps.includes("reqs_all"));

  async function toggle() {
    setBusy(true);
    setErr(null);
    try {
      await post(`/api/roles/${reqId}/${lock ? "unlock" : "lock"}`, { target });
      onChange();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (lock) {
    const who = lock.locked_by_name || humanizeEmail(lock.locked_by);
    return (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
        <span
          title={`Locked by ${lock.locked_by}${lock.locked_at ? ` · ${new Date(lock.locked_at).toLocaleString()}` : ""}`}
          style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, fontWeight: 700, padding: "3px 10px", borderRadius: 999, background: "var(--surface-warn)", color: "var(--warn-ink)", border: "1px solid var(--warn-border)" }}
        >
          🔒 {label} locked · {who}
        </span>
        {canUnlock && (
          <button onClick={() => void toggle()} disabled={busy}
            style={{ height: 26, padding: "0 10px", borderRadius: 8, border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink-soft)", fontSize: 11.5, fontWeight: 700, cursor: "pointer", fontFamily: "inherit" }}>
            Unlock
          </button>
        )}
        {err && <span style={{ fontSize: 11, color: "var(--warn-ink)" }}>{err}</span>}
      </span>
    );
  }
  return (
    <button onClick={() => void toggle()} disabled={busy}
      title={`Lock the ${label.toLowerCase()} so it can't be changed until it's unlocked`}
      style={{ height: 26, padding: "0 10px", borderRadius: 8, border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink-soft)", fontSize: 11.5, fontWeight: 700, cursor: "pointer", fontFamily: "inherit", display: "inline-flex", alignItems: "center", gap: 5 }}>
      🔓 Lock {label.toLowerCase()}
    </button>
  );
}
