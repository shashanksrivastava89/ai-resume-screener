// Shared overlay primitives from the v3 design prototype: the confirm modal
// (title · quoted text · amber reason · question · Cancel/CTA) and the dark
// toast pill. Both are transcriptions of the prototype's confirmBox and toast —
// values verbatim, reusable everywhere.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { CSSProperties, ReactNode } from "react";

// Every overlay renders through a portal to <body>: `position: fixed` inside a
// transformed ancestor (the .rise animation, the sliding drawer) anchors to
// that ancestor instead of the viewport — the "dialogs appearing anywhere on
// the screen" bug. The container also flex-CENTERS the dialog.
export function OverlayRoot({ children, onDismiss, zIndex = 80, align = "center" }: {
  children: ReactNode; onDismiss?: () => void; zIndex?: number; align?: "center" | "top";
}) {
  const wrap: CSSProperties = {
    position: "fixed", inset: 0, zIndex, display: "flex", justifyContent: "center",
    alignItems: align === "center" ? "center" : "flex-start",
    ...(align === "top" ? { paddingTop: "clamp(40px, 12vh, 120px)" } : {}),
  };
  return createPortal(
    <div style={wrap}>
      {/* A real scrim (2026-09-06): the old 28% grey left the page glaring through
          the dialog, which the pilot read as "hard to read". */}
      <div onClick={onDismiss} style={{ position: "absolute", inset: 0, background: "rgba(20,22,26,0.48)" }} />
      {children}
    </div>,
    document.body,
  );
}

// --- ConfirmModal ---------------------------------------------------------------------
//
// The design's confirm box: used for critic blocks/warns on checklist rows and
// preferences, floor-topic removal acknowledgment, and the stale-scores gate.
// `canProceed: false` renders an informational block (Cancel only).

export interface ConfirmSpec {
  title: string;
  /** The thing being confirmed, rendered in quotes. */
  text?: string;
  /** The amber ⚠ reason block. */
  reason?: string;
  /** The plain question under the reason. */
  question?: ReactNode;
  cta?: string;
  canProceed?: boolean;
  onProceed?: () => void;
}

export function ConfirmModal({ spec, onCancel }: { spec: ConfirmSpec; onCancel: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <OverlayRoot onDismiss={onCancel}>
      <div
        role="dialog"
        aria-label={spec.title}
        className="rise"
        style={{
          position: "relative", width: "min(480px, 90%)", background: "var(--white)",
          border: "1px solid var(--border-subtle)", borderRadius: 16,
          boxShadow: "var(--shadow-lg)", padding: "20px 24px",
        }}
      >
        {/* Body copy is INK at a readable size (2026-09-06): the grey 12.5px question
            under a light scrim was the "not quite readable" dialog in pilot feedback. */}
        <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 10, letterSpacing: "-0.01em" }}>{spec.title}</div>
        {spec.text && (
          <div style={{ fontSize: 13.5, lineHeight: 1.6, color: "var(--ink)", marginBottom: 10 }}>
            “{spec.text}”
          </div>
        )}
        {spec.reason && (
          <div style={{ display: "flex", gap: 8, fontSize: 13, lineHeight: 1.55, color: "var(--warn-ink)", background: "var(--surface-warn)", borderRadius: 9, padding: "9px 12px", marginBottom: 12 }}>
            <span style={{ fontWeight: 700 }}>⚠</span>
            <span>{spec.reason}</span>
          </div>
        )}
        {spec.question && (
          <div style={{ fontSize: 13.5, color: "var(--ink)", lineHeight: 1.6, marginBottom: 16 }}>
            {spec.question}
          </div>
        )}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button
            onClick={onCancel}
            style={{ height: 36, padding: "0 16px", borderRadius: 999, border: "1px solid var(--border-strong)", background: "var(--white)", color: "var(--ink)", fontFamily: "inherit", fontSize: 13, fontWeight: 700, cursor: "pointer" }}
          >
            Cancel
          </button>
          {(spec.canProceed ?? true) && (
            <button
              onClick={() => spec.onProceed?.()}
              style={{ height: 36, padding: "0 18px", borderRadius: 999, border: 0, background: "var(--brand-green)", color: "#fff", fontFamily: "inherit", fontSize: 13, fontWeight: 700, cursor: "pointer" }}
            >
              {spec.cta ?? "Proceed"}
            </button>
          )}
        </div>
      </div>
    </OverlayRoot>
  );
}

// --- Toast ------------------------------------------------------------------------------
//
// The design's dark pill, top-center, 2.4s. One provider at the app root;
// pages call `const toast = useToast(); toast("v2 published")`.

const ToastContext = createContext<(message: string) => void>(() => {});

export function useToast(): (message: string) => void {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const toast = useCallback((msg: string) => {
    setMessage(msg);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMessage(null), 2400);
  }, []);

  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);

  return (
    <ToastContext.Provider value={toast}>
      {children}
      {message && (
        <div
          role="status"
          className="rise"
          style={{
            position: "fixed", top: 18, left: "50%", transform: "translateX(-50%)",
            zIndex: 90, background: "var(--surface-inverted)", color: "#fff", fontSize: 12.5,
            fontWeight: 700, padding: "9px 18px", borderRadius: 999,
            boxShadow: "var(--shadow-lg)", maxWidth: "min(680px, 90vw)",
          }}
        >
          {message}
        </div>
      )}
    </ToastContext.Provider>
  );
}
