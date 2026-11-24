// The workspace's shared tab contract (Addendum 11 Part D3): Workspace.tsx
// provides {role, blind, setBlind, reload} through the router Outlet; every tab
// reads it with useWorkspace(). Later prompts append tabs — never fork a second
// tab mechanism.
import { useOutletContext } from "react-router-dom";
import type { RoleDetail } from "../../types";

export interface WorkspaceContext {
  /** The role detail payload (refetched by reload()). */
  role: RoleDetail;
  /** Blind review — owned by the workspace header so it follows across tabs. */
  blind: boolean;
  setBlind: (v: boolean) => void;
  /** Refetch the role detail (stage/counts) after a tab changes state. */
  reload: () => void;
  /** Review drawer width (px), owned by the workspace so it persists across
   *  tabs within one req and resets when the req changes. null = default. */
  drawerWidth: number | null;
  setDrawerWidth: (w: number | null) => void;
  /** The design's "ground shifted" confirm: paused decisions, the header's
   *  stale chip and the Shortlist gate all open the SAME modal; proceeding
   *  primes the Candidates tab (the re-screen is the ordinary screen click). */
  openStaleGate: () => void;
  /** Read-only workspace (2026-07-21): a viewer with reqs_read but no recruiter
   *  cap (Legal, from the audit trail) — every mutation control is hidden/disabled. */
  readOnly: boolean;
  /** The requisition is closed (2026-10-16): no new hiring work — screening,
   *  uploads, decisions, checklist publishes and sharing are frozen (the server
   *  answers 409); the record, comments and notes stay open. */
  closed: boolean;
}

export function useWorkspace(): WorkspaceContext {
  return useOutletContext<WorkspaceContext>();
}

/** The sibling-tab path inside this requisition's workspace. */
export function tabPath(reqId: string, tab: string): string {
  return `/reqs/${reqId}/${tab}`;
}
