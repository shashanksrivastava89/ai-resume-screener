// The score-composition chart — a faithful React transcription of the v3
// design prototype's `scoreChart` SVG (geometry, zones, guides, bars, the
// compare marker and the "held at fence" note, value-for-value). One shared
// component powers the drawer's "How N was built", the Scoring model page's
// "What a score is made of" and the live calculator.

export interface ScoreParts {
  base: number;
  net: number;
  pref: number;
  final: number;
  /** "Strong" | "Possible" | anything else = weak styling */
  band: string;
  clamped?: boolean;
}

export interface ChartBounds {
  strongAt: number;
  possibleAt: number;
  clampMin: number;
  clampMax: number;
}

const fmt = (v: number) => String(Math.round(v * 10) / 10);
const sgn = (v: number) => (v >= 0 ? "+" : "−") + fmt(Math.abs(v));

export default function ScoreChart({
  parts, bounds, compare, highlight,
}: {
  parts: ScoreParts;
  bounds: ChartBounds;
  /** Published-model result to mark as "was N" on the Final row. */
  compare?: ScoreParts | null;
  /** "base" | "net" | "pref" — softly highlights one row (open card). */
  highlight?: string | null;
}) {
  const { clampMin: minV, clampMax: maxV } = bounds;
  const span = Math.max(1, maxV - minV);
  const W = 344, padL = 12, padR = 12, plotW = W - padL - padR;
  const X = (v: number) => padL + ((Math.max(minV, Math.min(maxV, v)) - minV) / span) * plotW;
  const topPad = 26, barH = 22, rowGap = 18;
  const netCol = parts.net >= 0 ? "var(--accent-blue)" : "var(--status-warning)";
  const bandCol = parts.band === "Strong" ? "var(--hue-green-ink)"
    : parts.band === "Possible" ? "var(--hue-amber-ink)" : "var(--hue-slate-ink)";

  const rows = [
    { k: "base", label: "Checklist base", from: 0, to: parts.base, color: "var(--brand-green)", val: fmt(parts.base), total: false, faint: false },
    { k: "net", label: "AI signals", from: parts.base, to: parts.base + parts.net, color: netCol, val: sgn(parts.net), total: false, faint: false },
    { k: "pref", label: "Preferences", from: parts.base + parts.net, to: parts.base + parts.net + parts.pref, color: "var(--accent-yellow)", val: sgn(parts.pref), total: false, faint: parts.pref === 0 },
    { k: "final", label: "Final score", from: 0, to: parts.final, color: bandCol, val: fmt(parts.final), total: true, faint: false },
  ];
  const plotBottom = topPad + rows.length * barH + (rows.length - 1) * rowGap;
  const axisY = plotBottom + 7;
  const H = axisY + 16 + (compare ? 8 : 0);
  const showCompare = compare && fmt(compare.final) !== fmt(parts.final);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", overflow: "visible" }} role="img" aria-label="Score composition">
      {/* band zones */}
      <rect x={X(minV)} y={topPad - 5} width={Math.max(0, X(bounds.possibleAt) - X(minV))} height={plotBottom - topPad + 10} style={{ fill: "var(--neutral-100)" }} />
      <rect x={X(bounds.possibleAt)} y={topPad - 5} width={Math.max(0, X(bounds.strongAt) - X(bounds.possibleAt))} height={plotBottom - topPad + 10} style={{ fill: "rgba(45,140,196,0.10)" }} />
      <rect x={X(bounds.strongAt)} y={topPad - 5} width={Math.max(0, X(maxV) - X(bounds.strongAt))} height={plotBottom - topPad + 10} style={{ fill: "rgba(0,166,81,0.11)" }} />
      {/* threshold guides */}
      {([["Possible", bounds.possibleAt, "#16729E"], ["Strong", bounds.strongAt, "var(--brand-green-deep)"]] as const).map(([label, v, col]) => (
        <g key={label}>
          <line x1={X(v)} x2={X(v)} y1={topPad - 5} y2={plotBottom + 5} style={{ stroke: col }} strokeWidth={1} strokeDasharray="3 3" />
          <text x={X(v)} y={topPad - 11} fontSize={9} fontWeight={700} style={{ fill: col }} textAnchor="middle">{label} {fmt(v)}</text>
        </g>
      ))}
      {/* bars */}
      {rows.map((r, i) => {
        const y = topPad + i * (barH + rowGap);
        const x1 = Math.min(X(r.from), X(r.to));
        const w = Math.max(2.5, Math.abs(X(r.to) - X(r.from)));
        const rightward = X(r.to) >= X(r.from);
        const hot = highlight != null && highlight === r.k;
        return (
          <g key={r.k}>
            {hot && <rect x={padL - 6} y={y - 16} width={plotW + 12} height={barH + 20} rx={6} style={{ fill: "rgba(34,34,34,0.07)" }} />}
            <text x={padL} y={y - 5} fontSize={9.5} fontWeight={700} style={{ fill: hot ? "var(--ink)" : "var(--ink-soft)" }}>{r.label}</text>
            {!r.total && (
              <line x1={X(r.from)} x2={X(r.from)} y1={y - (i > 0 ? rowGap : 0)} y2={y + barH} style={{ stroke: "var(--border-strong)" }} strokeWidth={1} strokeDasharray="2 2" />
            )}
            <rect x={x1} y={y} width={w} height={barH} rx={4} style={{ fill: r.color, opacity: r.faint ? 0.35 : 1 }} />
            <text
              x={rightward ? x1 + w + 5 : x1 - 5}
              y={y + barH - 6.5}
              fontSize={11}
              fontWeight={700}
              fontFamily="monospace"
              style={{ fill: r.total ? "var(--ink)" : r.color }}
              textAnchor={rightward ? "start" : "end"}
            >
              {r.val}
            </text>
          </g>
        );
      })}
      {/* axis */}
      <line x1={padL} x2={W - padR} y1={axisY} y2={axisY} style={{ stroke: "var(--border-subtle)" }} strokeWidth={1} />
      <text x={padL} y={axisY + 12} fontSize={8.5} fontWeight={700} style={{ fill: "var(--ink-muted)" }}>{fmt(minV)}</text>
      <text x={W - padR} y={axisY + 12} fontSize={8.5} fontWeight={700} style={{ fill: "var(--ink-muted)" }} textAnchor="end">{fmt(maxV)}</text>
      {parts.clamped && (
        <text x={(padL + W - padR) / 2} y={axisY + 12} fontSize={8.5} fontWeight={700} style={{ fill: "var(--status-warning)" }} textAnchor="middle">held at fence</text>
      )}
      {/* the "was N" compare marker on the Final row */}
      {showCompare && (() => {
        const fy = topPad + 3 * (barH + rowGap);
        const cx = X(compare!.final);
        return (
          <g>
            <line x1={cx} x2={cx} y1={fy - 3} y2={fy + barH + 3} style={{ stroke: "var(--ink-muted)" }} strokeWidth={1.5} strokeDasharray="2 2" />
            <text x={cx} y={fy + barH + 14} fontSize={8.5} fontWeight={700} style={{ fill: "var(--ink-muted)" }} textAnchor="middle">was {fmt(compare!.final)}</text>
          </g>
        );
      })()}
    </svg>
  );
}
