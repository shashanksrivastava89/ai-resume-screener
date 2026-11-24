// The Review tab's working state, kept across tab switches (2026-09-06, pilot
// feedback item 12).
//
// A recruiter ticks twenty candidates, opens Candidates to check a file, comes
// back — and the selection is gone. Losing that is the most expensive kind of loss
// on this screen, so the selection and the view filters live in sessionStorage,
// per requisition: they survive a tab switch and a reload, and die with the browser
// tab, which is the right lifetime for "what I was in the middle of".
//
// What is NOT persisted, deliberately: the location lens (nearOnly). A distance
// filter that silently stayed on would quietly hide people (see ReviewTab); the
// decision/band/gate filters are visible as pressed pills and the "N of M shown"
// readout makes any narrowing explicit, so they are safe to keep.
//
// Best-effort throughout: a private window, disabled storage or a corrupt entry
// degrades to "nothing remembered", never to an error.

export interface ReviewViewState {
  /** Selected candidate ids (the bulk-action selection). */
  sel: string[];
  /** The decision-status filter: all | awaiting | forward | discuss | not. */
  tab: string;
  /** The band filter: all | Strong | Possible | weak. */
  band: string;
  /** The hard-gate filter: all | passed | failed. */
  gate: string;
  /** The name search. */
  query: string;
  /** rank | name | decided. */
  sortMode: string;
}

const key = (reqId: string) => `rs.review.${reqId}`;

function isState(v: unknown): v is ReviewViewState {
  if (!v || typeof v !== "object") return false;
  const s = v as ReviewViewState;
  return Array.isArray(s.sel) && s.sel.every((x) => typeof x === "string")
    && typeof s.tab === "string" && typeof s.band === "string" && typeof s.gate === "string"
    && typeof s.query === "string" && typeof s.sortMode === "string";
}

export function loadReviewState(reqId: string): ReviewViewState | null {
  try {
    const raw = sessionStorage.getItem(key(reqId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!isState(parsed)) throw new Error("bad shape");
    return parsed;
  } catch {
    try { sessionStorage.removeItem(key(reqId)); } catch { /* ignore */ }
    return null;
  }
}

export function saveReviewState(reqId: string, state: ReviewViewState): void {
  try {
    sessionStorage.setItem(key(reqId), JSON.stringify(state));
  } catch {
    /* quota / disabled storage — remembering is a convenience, never required */
  }
}

export function clearReviewState(reqId: string): void {
  try { sessionStorage.removeItem(key(reqId)); } catch { /* ignore */ }
}
