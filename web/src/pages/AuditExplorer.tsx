// The Audit Explorer (P46, restyled to the v3 design) at /audit — the
// runs_audit-gated home for "every run, decision, rule change and data event,
// searchable". Five tabs over the P45 explorer API; the region lens follows
// the app-wide picker; ?req= deep-links straight to a requisition card (from
// History's "View audit"). The Reports rail rides beside every tab.
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Page } from "../layout/AppShell";
import { Tabs } from "../components/ui";
import { IconSearch } from "../components/icons";
import { useRegionLens } from "../RegionContext";
import EventFeed from "../components/audit/EventFeed";
import CandidateTimeline from "../components/audit/CandidateTimeline";
import ReqAuditCards from "../components/audit/ReqAuditCards";
import RulesLog from "../components/audit/RulesLog";
import RetentionPanel from "../components/audit/RetentionPanel";
import ReportsRail from "../components/audit/ReportsRail";

const TABS = [
  { key: "all", label: "All" },
  { key: "candidates", label: "Candidates" },
  { key: "requisitions", label: "Requisitions" },
  { key: "rules", label: "Rules & criteria" },
  { key: "retention", label: "PII & retention" },
];

export default function AuditExplorer() {
  const [params] = useSearchParams();
  const deepReq = params.get("req");
  const { lens } = useRegionLens();
  // a ?req= deep link lands on the Requisitions tab with that card selected
  const [active, setActive] = useState<string>(deepReq ? "requisitions" : "all");
  const [q, setQ] = useState("");

  return (
    <Page max={1160}>
      <h1 style={{ fontSize: 24, letterSpacing: "-0.01em", margin: "0 0 4px" }}>Audit &amp; History</h1>
      <p style={{ fontSize: 13, color: "var(--ink-soft)", margin: "0 0 16px" }}>
        Every run, decision, rule change and data event — searchable. Viewing here is itself logged.
      </p>

      {/* the design's prominent search — green-bordered, full width, above the tabs */}
      <div style={{ position: "relative", marginBottom: 12 }}>
        <span style={{ position: "absolute", left: 13, top: 13, color: "var(--brand-green-deep)" }}><IconSearch size={18} /></span>
        <input
          value={q}
          onChange={(e) => { setQ(e.target.value); if (active !== "all" && active !== "candidates") setActive("all"); }}
          placeholder="Search a candidate, requisition, rule or data event…"
          style={{ width: "100%", height: 44, border: "1.5px solid var(--brand-green)", borderRadius: 12, padding: "0 14px 0 40px", fontSize: 14, background: "var(--white)", color: "var(--ink)", boxShadow: "var(--shadow-sm)", fontFamily: "inherit" }}
        />
      </div>

      <div style={{ marginBottom: 16 }}>
        <Tabs active={active} onSelect={setActive} tabs={TABS} />
      </div>

      {/* the Reports rail rides beside every tab (the design's two-column frame) */}
      <div style={{ display: "flex", gap: 20, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 560px", minWidth: 0 }}>
          {active === "all" && <EventFeed q={q} region={lens} />}
          {active === "candidates" && <CandidateTimeline region={lens} seedQuery={q} />}
          {active === "requisitions" && <ReqAuditCards selectedReq={deepReq} region={lens} />}
          {active === "rules" && <RulesLog region={lens} />}
          {active === "retention" && <RetentionPanel region={lens} />}
        </div>
        <div style={{ flex: "0 0 300px" }}>
          <ReportsRail region={lens} />
        </div>
      </div>
    </Page>
  );
}
