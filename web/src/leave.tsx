// Unsaved-changes guard (2026-09-06, pilot feedback item 10).
//
// The checklist editor autosaves a DRAFT to the browser, so leaving never loses
// the words — but it does leave them unpublished, and users kept walking away
// believing they had published. Every in-app way out of a dirty editor (a tab, the
// sidebar, the back link) now asks first; closing or refreshing the tab gets the
// browser's own prompt via `beforeunload`.
//
// The app uses <BrowserRouter> (not a data router), so react-router's useBlocker
// is unavailable; instead editors REGISTER a dirty-check here and the few
// navigation sites call `confirmLeave(go)` before navigating. Anything that does
// not go through those sites (a deep link typed into the address bar) still gets
// the beforeunload prompt.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ConfirmModal } from "./components/overlays";

type Check = () => string | null; // a message when dirty, null when clean

const registry = new Set<Check>();

/** The first dirty message across every registered editor, or null. */
export function pendingLeaveMessage(): string | null {
  for (const check of registry) {
    const msg = check();
    if (msg) return msg;
  }
  return null;
}

/** Register an editor's dirty state. `message` is what the confirm says; pass null
 *  when clean. Re-registers on every change so the check always reads live state. */
export function useLeaveGuard(message: string | null): void {
  const ref = useRef<string | null>(message);
  ref.current = message;
  useEffect(() => {
    const check: Check = () => ref.current;
    registry.add(check);
    const onUnload = (e: BeforeUnloadEvent) => {
      if (!ref.current) return;
      e.preventDefault();
      e.returnValue = ""; // the browser shows its own wording
    };
    window.addEventListener("beforeunload", onUnload);
    return () => {
      registry.delete(check);
      window.removeEventListener("beforeunload", onUnload);
    };
  }, []);
}

type Confirm = (go: () => void) => void;
const Ctx = createContext<Confirm>((go) => go());

/** Ask before leaving if any editor is dirty; otherwise navigate at once. */
export function useConfirmLeave(): Confirm {
  return useContext(Ctx);
}

export function LeaveGuardProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<{ message: string; go: () => void } | null>(null);
  const confirm = useCallback<Confirm>((go) => {
    const message = pendingLeaveMessage();
    if (!message) { go(); return; }
    setPending({ message, go });
  }, []);
  return (
    <Ctx.Provider value={confirm}>
      {children}
      {pending && (
        <ConfirmModal
          spec={{
            title: "Leave without publishing?",
            reason: pending.message,
            question: "Your edits are saved as a draft on this browser and will be offered back to you here — but they are not published, so screening still uses the previous version.",
            cta: "Leave anyway",
            canProceed: true,
            onProceed: () => { const go = pending.go; setPending(null); go(); },
          }}
          onCancel={() => setPending(null)}
        />
      )}
    </Ctx.Provider>
  );
}
