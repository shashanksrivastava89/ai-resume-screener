import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { get } from "./api";
import { seed } from "./hooks";
import type { Me } from "./types";

/** What the bootstrap payload carries, and where each part belongs in the SWR cache.
 *  The individual endpoints still exist and still work; this only spares the shell
 *  from asking for four things the server can hand over with the fifth. */
const BOOTSTRAP_PARTS: Record<string, string> = {
  features: "/api/features",
  directory: "/api/people/directory",
  notifications: "/api/notifications",
  active_screens: "/api/screen/active",
};

interface MeCtxValue {
  me: Me | null;
  refreshMe: () => void;
}

const MeCtx = createContext<MeCtxValue>({ me: null, refreshMe: () => {} });

export function MeProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  // Re-fetchable: publishing rules/packs reopens the review, and recording the
  // HR + Legal sign-off clears it — both need the shell banner to update.
  const refreshMe = useCallback(() => {
    // One request for the whole shell (2026-09-10). Opening any page used to fire
    // seven, in two dependent waves, each wave a full round trip before anything
    // could render. A part the caller may not read is simply absent, so this reads
    // whatever came back and seeds only that.
    get<Record<string, unknown>>("/api/bootstrap")
      .then((payload) => {
        setMe((payload.me ?? null) as Me | null);
        for (const [key, path] of Object.entries(BOOTSTRAP_PARTS)) seed(path, payload[key]);
      })
      .catch(() => {
        // An older server, or a shape we did not expect: the individual endpoints
        // are still there, so fall back to the one thing the shell cannot start
        // without and let every component fetch its own.
        get<Me>("/api/me").then(setMe).catch(() => setMe(null));
      });
  }, []);
  useEffect(() => { refreshMe(); }, [refreshMe]);
  return <MeCtx.Provider value={{ me, refreshMe }}>{children}</MeCtx.Provider>;
}

export function useMe(): Me | null {
  return useContext(MeCtx).me;
}

export function useMeRefresh(): () => void {
  return useContext(MeCtx).refreshMe;
}
