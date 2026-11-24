import { lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";
import { useMe } from "./MeContext";
import AppShell from "./layout/AppShell";
import Requisitions from "./pages/Requisitions";
import NewRequisition from "./pages/NewRequisition";
import { workspaceRoutes } from "./pages/workspace/Workspace";

// The recruiter hot path (Requisitions, New, the workspace) loads eagerly; the
// heavier admin/governance pages are code-split so they don't bloat the initial
// bundle. A single <Suspense> in AppShell covers them all.
const Scoring = lazy(() => import("./pages/Scoring"));
const Jurisdictions = lazy(() => import("./pages/Jurisdictions"));
const Access = lazy(() => import("./pages/Access"));
const AuditExplorer = lazy(() => import("./pages/AuditExplorer"));
const DataPage = lazy(() => import("./pages/DataPage"));
const Flags = lazy(() => import("./pages/Flags"));
const MyPreferences = lazy(() => import("./pages/MyPreferences"));
const Approvals = lazy(() => import("./pages/Approvals"));

// Capability guard (P37): a specific capability, not just "any admin" — every
// page names the exact capability that opens it (the API enforces the same).
function RequireCap({ cap, children }: { cap: string; children: ReactNode }) {
  const me = useMe();
  if (me === null) return null;
  if (!(me.capabilities ?? []).includes(cap)) return <Navigate to="/reqs" replace />;
  return <>{children}</>;
}

// Any-of guard (P51): the Approvals page serves two approver capabilities.
function RequireAnyCap({ caps, to = "/reqs", children }: { caps: string[]; to?: string; children: ReactNode }) {
  const me = useMe();
  if (me === null) return null;
  if (!caps.some((c) => (me.capabilities ?? []).includes(c))) return <Navigate to={to} replace />;
  return <>{children}</>;
}

// The landing page depends on what the caller can see (2026-07-19): recruiters
// and HMs land on Requisitions; Legal (no reqs, no prefs) lands on Approvals,
// then Scoring, then Audit — the first page their role can open.
function Home() {
  const me = useMe();
  if (me === null) return null;
  const caps = me.capabilities ?? [];
  if (caps.includes("recruiter") || caps.includes("hm_take")) return <Navigate to="/reqs" replace />;
  if (caps.includes("changes_approve") || caps.includes("prefs_approve")) return <Navigate to="/approvals" replace />;
  if (caps.includes("rules_view")) return <Navigate to="/scoring" replace />;
  if (caps.includes("runs_audit")) return <Navigate to="/audit" replace />;
  return <Navigate to="/reqs" replace />;
}

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Home />} />
        <Route path="/reqs" element={<RequireAnyCap caps={["recruiter", "hm_take", "reqs_read"]} to="/"><Requisitions /></RequireAnyCap>} />
        <Route path="/reqs/new" element={<RequireCap cap="recruiter"><NewRequisition /></RequireCap>} />
        {workspaceRoutes()}
        <Route path="/preferences" element={<RequireCap cap="prefs_own"><MyPreferences /></RequireCap>} />
        <Route path="/approvals" element={<RequireAnyCap caps={["prefs_approve", "changes_approve"]}><Approvals /></RequireAnyCap>} />
        <Route path="/preference-approvals" element={<Navigate to="/approvals" replace />} />
        <Route path="/scoring/:tab?" element={<RequireCap cap="rules_view"><Scoring /></RequireCap>} />
        <Route path="/rules" element={<Navigate to="/scoring" replace />} />
        <Route path="/jurisdictions" element={<RequireCap cap="rules_view"><Jurisdictions /></RequireCap>} />
        <Route path="/access" element={<RequireCap cap="access_view"><Access /></RequireCap>} />
        <Route path="/flags" element={<RequireCap cap="flags_view"><Flags /></RequireCap>} />
        <Route path="/audit" element={<RequireCap cap="runs_audit"><AuditExplorer /></RequireCap>} />
        <Route path="/data" element={<RequireCap cap="data_manage"><DataPage /></RequireCap>} />
        <Route path="/runs" element={<Navigate to="/audit" replace />} />
        <Route path="*" element={<Home />} />
      </Route>
    </Routes>
  );
}
