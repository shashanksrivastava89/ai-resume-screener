// Reports rail (P46): the three ready CSV reports (download + print) and the
// "Adverse-impact monitor — Phase 2" chip. CSVs come from the guarded explorer
// route; region= follows the current lens.
import { get } from "../../api";
import { useFetch } from "../../hooks";
import type { AuditReport, ReportsResponse } from "../../types";

async function downloadCsv(key: string, region: string | null) {
  const qs = region ? `?region=${encodeURIComponent(region)}` : "";
  const text = await get<string>(`/api/audit/reports/${key}.csv${qs}`);
  const href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = href;
  a.download = `${key}.csv`;
  a.click();
  URL.revokeObjectURL(href);
}

export default function ReportsRail({ region }: { region: string | null }) {
  const { data } = useFetch<ReportsResponse>("/api/audit/reports");
  const reports = data?.reports ?? [];

  // The design's compact rows: label left, "CSV · PDF" links right; the
  // not-yet-live monitor renders dimmed with its Phase 2 chip.
  return (
    <aside style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 14, padding: "16px 18px" }}>
      <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--ink-soft)", marginBottom: 12 }}>
        Reports
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 9, fontSize: 12.5 }}>
        {reports.map((r: AuditReport) => (
          <div key={r.key} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, color: r.status === "Phase 2" ? "var(--ink-muted)" : "var(--ink)" }}>
            <span>{r.label}</span>
            {r.status === "Phase 2" ? (
              <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 6, background: "var(--neutral-150)", whiteSpace: "nowrap" }}>
                Phase 2
              </span>
            ) : (
              <span style={{ fontSize: 11, fontWeight: 700, whiteSpace: "nowrap" }}>
                <button onClick={() => downloadCsv(r.key, region)} style={{ background: "none", border: 0, padding: 0, fontFamily: "inherit", fontSize: 11, fontWeight: 700, color: "var(--brand-green-deep)", cursor: "pointer" }}>CSV</button>
                <span style={{ color: "var(--ink-muted)" }}> · </span>
                <button onClick={() => window.print()} style={{ background: "none", border: 0, padding: 0, fontFamily: "inherit", fontSize: 11, fontWeight: 700, color: "var(--brand-green-deep)", cursor: "pointer" }}>PDF</button>
              </span>
            )}
          </div>
        ))}
      </div>
    </aside>
  );
}
