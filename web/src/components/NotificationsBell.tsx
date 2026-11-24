// The in-app notification bell + panel (2026-07-20). Every `notify` event
// addressed to the signed-in user, shown with the actor's real NAME, GROUPED by
// requisition (latest activity first) and SEARCHABLE. The panel is a persistent,
// NON-MODAL callout: it stays open while you click notifications to navigate the
// app (most of the frame stays visible + interactive) and closes only on ✕ or
// the bell. Unread rows stay highlighted and counted until the panel is closed.
// Polls quietly; delivery (email/Teams) stays a §12 concern.
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { get, getPolled } from "../api";
import { useMe } from "../MeContext";
import { keepIfSame, takeFresh, useHiddenAwarePoll } from "../hooks";
import type { AppNotification } from "../types";

const SEEN_KEY = "rs.notifications.seen";
const POLL_MS = 30_000;

function seenTs(user: string): string {
  try { return localStorage.getItem(`${SEEN_KEY}.${user}`) ?? ""; } catch { return ""; }
}
function markSeen(user: string, ts: string) {
  try { localStorage.setItem(`${SEEN_KEY}.${user}`, ts); } catch { /* private mode */ }
}

const KIND_ICON: Record<string, string> = {
  change_approved: "✓", change_declined: "✕",
  pref_approved: "✓", pref_declined: "✕",
  suggestion_created: "✎", suggestion_accepted: "✓", suggestion_declined: "✕",
  shortlist_shared: "☰", hm_decisions_updated: "☰", hm_comments_added: "💬",
  comment_added: "💬", comment_mention: "@", hm_takes_sent: "☰", hm_invited: "✉", hm_review_requested: "✉",
};

/** Where a notification's click lands (2026-07-19): always the exact page —
 *  and, via ?focus=, the exact element — that caused it. Never just Overview. */
export function notificationTarget(n: AppNotification): string | null {
  const d = (n.data ?? {}) as Record<string, unknown>;
  const ids = Array.isArray(d.candidate_ids) ? (d.candidate_ids as string[]) : [];
  if (n.req_id) {
    const base = `/reqs/${n.req_id}`;
    switch (n.kind) {
      case "suggestion_created":
      case "suggestion_accepted":
      case "suggestion_declined":
        return `${base}/checklist${d.suggestion_id ? `?focus=sugg:${d.suggestion_id}` : ""}`;
      case "hm_invited":
        return `${base}/checklist`;
      case "comment_added":
      case "comment_mention":
        return `${base}/review${d.candidate_id
          ? `?focus=comment:${d.candidate_id}${d.comment_id ? `:${d.comment_id}` : ""}` : ""}`;
      case "hm_comments_added":
        return ids.length === 1
          ? `${base}/review?focus=comment:${ids[0]}`
          : `${base}/review${ids.length ? `?focus=cand:${ids.join(",")}` : ""}`;
      case "hm_decisions_updated":
        return `${base}/review${ids.length ? `?focus=cand:${ids.join(",")}` : ""}`;
      case "shortlist_shared":
      case "hm_takes_sent":
      case "hm_review_requested":
        return `${base}/review`;
      default:
        return base; // anything unmapped still opens the requisition
    }
  }
  // Scoring-model change outcomes land on the Scoring-model tab of Approvals
  // (2026-07-20); preference outcomes on the Preferences tab.
  if (n.kind === "change_approved" || n.kind === "change_declined") return "/approvals?tab=model";
  if (n.kind === "pref_approved" || n.kind === "pref_declined") return "/approvals?tab=prefs";
  return null;
}

const TARGET_LABEL: Record<string, string> = {
  checklist: "open the checklist →", review: "open the review →",
  approvals: "open approvals →", preferences: "open your preferences →",
};
function targetLabel(target: string): string {
  const tail = target.split("?")[0].split("/").pop() ?? "";
  return TARGET_LABEL[tail] ?? "open the requisition →";
}

interface Group { key: string; title: string; items: AppNotification[]; latest: string }

export default function NotificationsBell() {
  const me = useMe();
  const nav = useNavigate();
  const [items, setItems] = useState<AppNotification[]>([]);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [seen, setSeen] = useState(() => seenTs(me?.user ?? ""));
  const panelRef = useRef<HTMLDivElement>(null);
  const bellRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!me?.user) return;
    setSeen(seenTs(me.user));
    let live = true;
    // The first read is immediate: an unread badge on arrival is the point of a bell.
    // The shell's bootstrap already carries it, so on a fresh page load this costs
    // nothing (2026-09-10).
    const seeded = takeFresh<{ notifications: AppNotification[] }>("/api/notifications");
    if (seeded) { setItems(seeded.notifications); return () => { live = false; }; }
    void (async () => {
      try {
        const r = await get<{ notifications: AppNotification[] }>("/api/notifications");
        if (live) setItems(r.notifications);
      } catch { /* transient */ }
    })();
    return () => { live = false; };
  }, [me?.user]);

  useHiddenAwarePoll(async () => {
    if (!me?.user) return;
    const r = await getPolled<{ notifications: AppNotification[] }>("/api/notifications");
    setItems(keepIfSame(r.notifications));
  }, POLL_MS, [me?.user]);

  const unread = items.filter((n) => n.ts > seen).length;

  // Group by requisition, latest-active group first; a group's rows newest-first.
  const groups: Group[] = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const match = (n: AppNotification) =>
      !needle
      || (n.summary ?? "").toLowerCase().includes(needle)
      || (n.from_name ?? "").toLowerCase().includes(needle)
      || (n.from ?? "").toLowerCase().includes(needle)
      || (n.req_title ?? "").toLowerCase().includes(needle);
    const by = new Map<string, Group>();
    for (const n of items) {
      if (!match(n)) continue;
      const key = n.req_id ?? "__general__";
      const g = by.get(key)
        ?? { key, title: n.req_title ?? (n.req_id ? n.req_id : "General"), items: [], latest: "" };
      g.items.push(n);
      if (n.ts > g.latest) g.latest = n.ts;
      by.set(key, g);
    }
    const arr = Array.from(by.values());
    arr.forEach((g) => g.items.sort((a, b) => (a.ts < b.ts ? 1 : -1)));
    arr.sort((a, b) => (a.latest < b.latest ? 1 : -1));
    return arr;
  }, [items, q]);

  function closePanel() {
    setOpen(false);
    // everything shown is now "seen" — the count clears; new rows re-highlight.
    if (me?.user && items[0]) { markSeen(me.user, items[0].ts); setSeen(items[0].ts); }
  }

  // Click OUTSIDE the panel closes it (2026-07-21). A click on a notification row
  // is inside the panel — it navigates and the panel stays open; the bell button
  // toggles itself, so it's excluded here to avoid a double-fire.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (panelRef.current?.contains(t) || bellRef.current?.contains(t)) return;
      closePanel();
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  return (
    <>
      <button
        ref={bellRef}
        onClick={() => (open ? closePanel() : setOpen(true))}
        aria-label={unread > 0 ? `Notifications — ${unread} new` : "Notifications"}
        style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", marginBottom: 10, padding: "6px 10px", border: "1px solid var(--border-subtle)", borderRadius: 8, background: open ? "var(--neutral-150)" : "var(--white)", color: "var(--ink-soft)", fontFamily: "inherit", fontSize: 11.5, fontWeight: 700, cursor: "pointer" }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        Notifications
        {unread > 0 && (
          <span style={{ marginLeft: "auto", minWidth: 18, height: 18, padding: "0 5px", borderRadius: 999, background: "var(--brand-green)", color: "#fff", fontSize: 10.5, fontWeight: 700, display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
            {unread}
          </span>
        )}
      </button>

      {open && (
        // A NON-MODAL floating callout docked bottom-left (above the sidebar
        // footer): no backdrop, so the app frame stays visible + interactive and
        // the panel stays open while you click through notifications.
        <div
          ref={panelRef}
          role="dialog"
          aria-label="Notifications"
          className="rise"
          style={{ position: "fixed", left: 14, bottom: 14, zIndex: 80, width: "min(384px, 92vw)", maxHeight: "76vh", display: "flex", flexDirection: "column", background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 16, boxShadow: "var(--shadow-lg)" }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, padding: "14px 16px 8px" }}>
            <div style={{ fontSize: 14.5, fontWeight: 700 }}>Notifications</div>
            <button onClick={closePanel} aria-label="Close notifications" style={{ width: 28, height: 28, borderRadius: "50%", border: "1px solid var(--border-subtle)", background: "var(--white)", color: "var(--ink-soft)", cursor: "pointer", fontSize: 12 }}>✕</button>
          </div>
          <div style={{ padding: "0 16px 8px" }}>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search notifications…"
              style={{ width: "100%", height: 32, border: "1px solid var(--border-subtle)", borderRadius: 8, padding: "0 10px", fontSize: 12.5, background: "var(--neutral-100)", color: "var(--ink)", fontFamily: "inherit" }}
            />
          </div>
          <div style={{ overflowY: "auto", padding: "2px 16px 14px" }}>
            {groups.length === 0 && (
              <div style={{ fontSize: 12.5, color: "var(--ink-soft)", padding: "14px 0" }}>
                {q ? "No notifications match your search." : "Nothing yet — approvals, comments, mentions and shortlist shares land here."}
              </div>
            )}
            {groups.map((g) => (
              <div key={g.key} style={{ marginTop: 10 }}>
                <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--ink-muted)", padding: "2px 2px 6px" }}>
                  {g.title}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {g.items.map((n, i) => {
                    const fresh = n.ts > seen;
                    const icon = KIND_ICON[n.kind] ?? "•";
                    const declineish = n.kind.endsWith("declined");
                    const target = notificationTarget(n);
                    return (
                      <button
                        key={`${n.ts}-${i}`}
                        onClick={() => { if (target) nav(target); }}  // stays open on navigate
                        style={{ display: "flex", gap: 10, alignItems: "flex-start", textAlign: "left", padding: "9px 11px", border: "1px solid var(--neutral-150)", borderRadius: 10, background: fresh ? "var(--brand-green-tint)" : "var(--white)", cursor: target ? "pointer" : "default", fontFamily: "inherit" }}
                      >
                        <span aria-hidden="true" style={{ flex: "0 0 auto", width: 22, height: 22, borderRadius: "50%", display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 11, fontWeight: 700, background: declineish ? "var(--neutral-150)" : "var(--brand-green-tint)", color: declineish ? "var(--ink-soft)" : "var(--brand-green-deep)" }}>
                          {icon}
                        </span>
                        <span style={{ minWidth: 0 }}>
                          <span style={{ display: "block", fontSize: 12.5, color: "var(--ink)", lineHeight: 1.5 }}>{n.summary}</span>
                          <span style={{ display: "block", fontSize: 11, color: "var(--ink-muted)", marginTop: 2 }}>
                            <span title={n.from}>{n.from_name || n.from}</span>
                            {" · "}{n.ts ? new Date(n.ts).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : ""}
                            {target ? ` · ${targetLabel(target)}` : ""}
                          </span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
