// Hiring-manager portal detection (P42) — dark-launched. The HM experience is
// driven entirely by capabilities (which the server suppresses when the
// hm_portal env flag is off), mirroring the backend's _hm_only(). We never
// branch on the raw role name except for the flag-off notice, where the
// capability is gone but the HiringManager role is still known.
import type { Me } from "./types";

/** A hiring manager whose portal is live: holds hm_take, is not a recruiter. */
export function isHmActive(me: Me | null): boolean {
  const caps = me?.capabilities ?? [];
  return caps.includes("hm_take") && !caps.includes("recruiter");
}

/** Signed in as a Hiring Manager (role known) — independent of the flag. */
export function isHmRole(me: Me | null): boolean {
  return (me?.roles ?? []).includes("HiringManager");
}

/** HM role known but the portal capability is suppressed (flag off) and the
 *  user has no recruiter fallback — show the "portal not enabled" notice
 *  instead of a broken requisitions shell. */
export function isHmPortalDark(me: Me | null): boolean {
  const caps = me?.capabilities ?? [];
  return isHmRole(me) && !caps.includes("hm_take") && !caps.includes("recruiter");
}
