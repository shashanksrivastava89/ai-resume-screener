// The hiring-rules explainer (2026-09-06, pilot feedback item 9): wherever the
// app names the governing law/pack ("DPDP Act 2023"), the name is now something
// you can open. Recruiters are not expected to know what an act asks of them —
// the pack's own plain-language obligations, the two retention duties and the
// monitoring note are what they need, and the pack already carries every one of
// them (policies/<code>.yaml). This reads them from GET /api/locations (cached),
// so the words on screen are the pack's words, never a copy that can drift.
//
// Displayed, never enforced — the obligations checklist is a list to act on, the
// same way the CLAUDE.md §1 contract has always described it.
import { Tip } from "./ui";
import { IconLaw } from "./icons";
import { useFetch } from "../hooks";
import type { LocationOption, LocationsFeed } from "../types";

export function useLocationsFeed(): LocationsFeed | undefined {
  const { data } = useFetch<LocationsFeed>("/api/locations");
  return data ?? undefined;
}

export function useLocationOption(code: string | null | undefined): LocationOption | null {
  const feed = useLocationsFeed();
  if (!feed || !code) return null;
  return feed.packs.find((l) => l.code === code) ?? null;
}

function days(n: number | null | undefined): string {
  if (!n) return "";
  if (n % 365 === 0) return `${n / 365} year${n / 365 === 1 ? "" : "s"}`;
  if (n % 30 === 0 && n < 365) return `${n / 30} months`;
  return `${n} days`;
}

/** The popover body — exported so the New-requisition form can show it inline. */
export function HiringRulesBody({ loc, law, processing }: {
  loc: LocationOption | null;
  law: string;
  /** Where this deployment stores and processes the data, in the deployment's own
   *  words. Shown only when it has said (PROCESSING_LOCATIONS) — a recruiter asking
   *  what a hiring rule means is exactly who wants to know where the data goes. */
  processing?: string | null;
}) {
  const obligations = loc?.obligations ?? [];
  return (
    <div style={{ maxWidth: 360 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span className="rules-icon"><IconLaw size={13} /></span>
        <strong style={{ fontSize: 13 }}>{law || loc?.law || "Hiring rules"}</strong>
        {loc?.label && <span style={{ fontSize: 11.5, color: "var(--ink-muted)" }}>· {loc.label}</span>}
      </div>
      {obligations.length > 0 ? (
        <>
          <div style={{ fontSize: 11, fontWeight: 800, letterSpacing: "0.05em", textTransform: "uppercase", color: "var(--ink-muted)", margin: "6px 0 4px" }}>
            What this asks of you
          </div>
          <ul style={{ margin: 0, paddingLeft: 16, display: "flex", flexDirection: "column", gap: 4, fontSize: 12.5, lineHeight: 1.45 }}>
            {obligations.map((o, i) => <li key={i}>{o}</li>)}
          </ul>
        </>
      ) : (
        <div style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>
          The rules pack for this location decides which AI signals apply, how data is kept
          and what candidates must be told.
        </div>
      )}
      {(loc?.keep_at_least_days || loc?.purge_by_days) && (
        <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 8, lineHeight: 1.45 }}>
          <strong style={{ color: "var(--ink)" }}>Data retention:</strong>
          {loc.keep_at_least_days ? ` audit record kept at least ${days(loc.keep_at_least_days)}` : ""}
          {loc.keep_at_least_days && loc.purge_by_days ? " ·" : ""}
          {loc.purge_by_days ? ` candidate details purged ${days(loc.purge_by_days)} after close` : ""}
          . Nothing deletes on its own.
        </div>
      )}
      {loc?.notes && (
        <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 6, lineHeight: 1.45 }}>{loc.notes}</div>
      )}
      {processing && (
        <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 8, lineHeight: 1.45 }}>
          <strong style={{ color: "var(--ink)" }}>Where this data is processed:</strong>{" "}
          {processing}
        </div>
      )}
    </div>
  );
}

/** The law name as an opener: hover or click shows what the pack means here. */
export default function HiringRules({ code, law, style, quiet = false }: {
  code: string | null | undefined;
  law: string;
  style?: React.CSSProperties;
  /** Muted (meta-line) styling instead of the chip. */
  quiet?: boolean;
}) {
  const feed = useLocationsFeed();
  const loc = useLocationOption(code);
  if (!law) return null;
  return (
    <Tip content={<HiringRulesBody loc={loc} law={law} processing={feed?.processing_locations} />}
         label={`Hiring rules — ${law}`} interactive maxWidth={380}>
      <button type="button" className={"rules-link" + (quiet ? " is-quiet" : "")} style={style}>
        <IconLaw size={12} />
        <span>{law}</span>
      </button>
    </Tip>
  );
}
