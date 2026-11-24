// Shared time formatting (P42). The activity rail and HM stamps render a
// requisition-local time next to the viewer's own — "the interview was 9am in
// Singapore, which is 6:30am for you". When the requisition zone equals the
// viewer's (or is unknown) there's no second clause.
export const VIEWER_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

function timeIn(d: Date, tz?: string): string {
  return d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
    ...(tz ? { timeZone: tz } : {}),
  });
}

/** "Jul 12, 09:00 GMT+8 · 06:30 your time" — the second clause only when the
 *  requisition zone differs from the viewer's. */
export function dualStamp(ts: string, reqTz?: string): string {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  const date = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const reqPart = timeIn(d, reqTz);
  if (!reqTz || reqTz === VIEWER_TZ) return `${date}, ${reqPart}`;
  const local = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return `${date}, ${reqPart} · ${local} your time`;
}
