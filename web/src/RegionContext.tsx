// The region ACCESS lens (P38): a session-wide "which region am I looking at"
// choice, surfaced by RegionPicker on the user chip and consumed by the
// Requisitions list and the Audit Explorer (sent as ?region= on their fetches).
//
// Region is an access lens, never a law lens — jurisdiction→policy resolution
// stays server-side. The lens only narrows what a caller already has access to;
// the server independently enforces the caller's grant, so this is a convenience,
// not a gate.
//
// WHERE IT OPENS (2026-09-12). It used to open on "All regions" always, because
// `localStorage.getItem(KEY) || null` cannot tell "I chose everything" from "I
// have never chosen" — so a recruiter in Budapest met every region's requisitions
// on every new device. An explicit All is now stored as the literal "all", which
// leaves absence meaning "no choice yet" and lets the server's derived home
// region (their own choice, else their country, else their only granted region)
// decide the opening view.
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useMe } from "./MeContext";

const KEY = "rs.regionLens";
const ALL = "all"; // an explicit "show me everything", distinct from "never chose"

interface RegionLensValue {
  /** The region to filter by; null = All regions. */
  lens: string | null;
  setLens: (v: string | null) => void;
  /** The server's derived default, and how it got there. */
  home: string | null;
  homeSource: string | null;
  /** True while the lens is the derived default rather than this person's choice. */
  isDefault: boolean;
  /** False until identity has landed — a fetch sent before this would go out
   *  unfiltered and then be repeated, which reads as a flash of other regions. */
  ready: boolean;
}

const Ctx = createContext<RegionLensValue>({
  lens: null, setLens: () => {}, home: null, homeSource: null,
  isDefault: true, ready: true,
});

export function RegionLensProvider({ children }: { children: ReactNode }) {
  const me = useMe();
  const [choice, setChoice] = useState<string | null | undefined>(() => {
    try {
      return localStorage.getItem(KEY) ?? undefined;   // undefined = never chose
    } catch {
      return undefined;
    }
  });

  const setLens = useCallback((v: string | null) => {
    setChoice(v ?? ALL);
    try {
      localStorage.setItem(KEY, v ?? ALL);
    } catch {
      /* private mode / storage disabled — the lens just isn't persisted */
    }
  }, []);

  const clearChoice = useCallback(() => {
    setChoice(undefined);
    try {
      localStorage.removeItem(KEY);
    } catch {
      /* nothing to clear */
    }
  }, []);

  const value = useMemo<RegionLensValue>(() => {
    const codes = me?.regions?.codes ?? [];
    const home = me?.regions?.home ?? null;
    const raw = choice === undefined ? home : (choice === ALL ? null : choice);
    // A saved or derived region the person cannot reach shows them nothing and
    // 403s the Audit Explorer. Falling back to "All regions" fails OPEN, which
    // for a display lens is the safe direction.
    const lens = raw && codes.length && !codes.includes(raw) ? null : raw;
    return {
      lens,
      setLens: (v: string | null) => (v === null && choice === undefined ? clearChoice() : setLens(v)),
      home,
      homeSource: me?.regions?.home_source ?? null,
      isDefault: choice === undefined,
      ready: me !== null,
    };
  }, [choice, me, setLens, clearChoice]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useRegionLens(): RegionLensValue {
  return useContext(Ctx);
}

/** Forget this person's stored choice — "use my default again". */
export function clearStoredLens(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nothing to clear */
  }
}
