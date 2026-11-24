// The hiring manager's take control (P42): Strong yes / Yes / No. A take is
// INPUT to the recruiter — never a decision — so it deliberately does NOT reuse
// the decision triad's Move-forward / Not-a-fit language. Clicking the active
// take again clears it.
export type Take = "strong_yes" | "yes" | "no";

const TAKES: { key: Take; label: string; bg: string; fg: string }[] = [
  { key: "strong_yes", label: "Strong yes", bg: "var(--hue-green-tint)", fg: "var(--hue-green-ink)" },
  { key: "yes", label: "Yes", bg: "var(--hue-blue-tint)", fg: "var(--hue-blue-ink)" },
  { key: "no", label: "No", bg: "var(--hue-slate-tint)", fg: "var(--hue-slate-ink)" },
];

export const TAKE_LABEL: Record<Take, string> = {
  strong_yes: "Strong yes",
  yes: "Yes",
  no: "No",
};

export default function HmTakeButtons({
  value,
  onPick,
  size = "md",
}: {
  value: Take | null;
  onPick: (take: Take) => void;
  size?: "sm" | "md";
}) {
  const h = size === "sm" ? 32 : 36;
  return (
    <div style={{ display: "flex", gap: 7 }} onClick={(e) => e.stopPropagation()}>
      {TAKES.map((t) => {
        const active = value === t.key;
        return (
          <button
            key={t.key}
            onClick={() => onPick(t.key)}
            aria-pressed={active}
            style={{
              height: h,
              padding: "0 13px",
              borderRadius: 999,
              border: active ? `1px solid ${t.fg}` : "1px solid var(--border-subtle)",
              background: active ? t.bg : "var(--white)",
              color: active ? t.fg : "var(--ink-soft)",
              fontFamily: "inherit",
              fontSize: 12.5,
              fontWeight: 700,
              cursor: "pointer",
              whiteSpace: "nowrap",
            }}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
