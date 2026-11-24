// People (2026-09-06): the one way the UI turns an email or UPN into a person.
//
// The engine stores the sign-in ADDRESS on every action, and on the hosted tenant
// that is a payroll id ("u120729@example.com") — nobody's name. The server
// now keeps a people directory (people.py) learned from sign-ins (the `name`
// claim), typed hiring-manager names and, when a deployment enables it, Microsoft
// Graph. This module reads that directory once (SWR-cached) and renders every
// "who" the same way: a name, the address on hover, and an avatar — a photo when
// the directory has one, else initials on a colour that is stable per person, so
// the same colleague looks the same on the dashboard, the trail and a comment.
//
// RULE: never render a raw `created_by` / `actor` / `author` / `reviewer` string.
// Use <Person>, or personName() for plain text (titles, CSV, prose).
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { get } from "./api";
import { useFetch } from "./hooks";
import { humanizeEmail } from "./names";
import type { PeopleSearch, Person as PersonRow } from "./types";

// The six meaning-free hues (tokens.css); rose is destructive-only and slate reads
// as "not a fit", so neither may become someone's face. Mirrors people.AVATAR_HUES.
const AVATAR_HUES = ["green", "violet", "blue", "teal", "amber", "orange"] as const;

/** A stable colour per address (FNV-1a over the lower-cased email). The server
 *  computes its own hue with SHA-1; when a directory row is present ITS hue wins,
 *  so the two never disagree on a known person. */
export function avatarHue(email: string): string {
  const s = (email ?? "").trim().toLowerCase();
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return AVATAR_HUES[h % AVATAR_HUES.length];
}

export function initialsOf(name: string, email = ""): string {
  const parts = (name ?? "").replace(/[-_]/g, " ").split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  const local = (email ?? "").split("@", 1)[0];
  return (local[0] ?? "?").toUpperCase();
}

/** "arjun.test" reads as a name; "u120729" does not. Decides whether humanising
 *  the address helps or harms. */
export function nameShaped(email: string): boolean {
  return /[._+-]/.test((email ?? "").split("@", 1)[0]);
}

export type PeopleIndex = Map<string, PersonRow>;

const EMPTY: PeopleIndex = new Map();

/** The directory, keyed by lower-cased address. Fetched once per session (the
 *  SWR cache dedupes every caller); missing or forbidden → empty, never an error
 *  surface — names fall back to the address. */
export function usePeople(): PeopleIndex {
  const { data } = useFetch<{ people: PersonRow[] }>("/api/people/directory");
  return useMemo(() => {
    if (!data?.people) return EMPTY;
    return new Map(data.people.map((p) => [p.email.toLowerCase(), p]));
  }, [data]);
}

/** The best name for an address, as plain text. `known` (a name the caller already
 *  holds, e.g. a stored author_name) wins; then the directory; then a humanised
 *  address when it is name-shaped; else the address itself — a payroll id shown as
 *  "U120729" would be worse than the address. */
export function personName(email: string | null | undefined, people: PeopleIndex = EMPTY, known?: string | null): string {
  const addr = (email ?? "").trim();
  if (known && known.trim()) return known.trim();
  if (!addr) return "";
  const hit = people.get(addr.toLowerCase());
  if (hit?.name) return hit.name;
  return nameShaped(addr) ? humanizeEmail(addr) : addr;
}

const SIZE: Record<"xs" | "sm" | "md" | "lg", number> = { xs: 20, sm: 26, md: 32, lg: 44 };

/** The avatar alone: a photo when known, else initials on the person's hue. */
export function Avatar({ email, name, photo, hue, size = "sm", style }: {
  email: string; name?: string; photo?: string | null; hue?: string;
  size?: "xs" | "sm" | "md" | "lg"; style?: CSSProperties;
}) {
  const px = SIZE[size];
  const h = hue || avatarHue(email);
  const label = name || email;
  if (photo) {
    return (
      <img src={photo} alt="" title={label} className="avatar"
           style={{ width: px, height: px, ...style }} />
    );
  }
  return (
    <span className={`avatar avatar-hue-${h}`} aria-hidden="true" title={label}
          style={{ width: px, height: px, fontSize: Math.round(px * 0.38), ...style }}>
      {initialsOf(name ?? "", email)}
    </span>
  );
}

/** A person, rendered the house way: [avatar] Name — address (and title) on hover.
 *  `you` swaps the name for "You" when the address is the viewer's. `known` is a
 *  name already on the record (author_name, hm.name) and wins over the directory. */
export function Person({ email, known, people, me, you = false, avatar = true, size = "xs", muted = false, style }: {
  email: string | null | undefined;
  known?: string | null;
  people?: PeopleIndex;
  /** The viewer's address, for `you`. */
  me?: string | null;
  you?: boolean;
  avatar?: boolean;
  size?: "xs" | "sm" | "md" | "lg";
  muted?: boolean;
  style?: CSSProperties;
}) {
  const addr = (email ?? "").trim();
  if (!addr) return <span style={{ color: "var(--ink-muted)", ...style }}>—</span>;
  const row = people?.get(addr.toLowerCase());
  const isMe = !!me && me.trim().toLowerCase() === addr.toLowerCase();
  const name = personName(addr, people, known);
  const label = you && isMe ? "You" : name;
  const title = [name !== addr ? addr : "", row?.job_title].filter(Boolean).join(" · ") || undefined;
  return (
    <span className="person" title={title} style={{ color: muted ? "var(--ink-soft)" : undefined, ...style }}>
      {avatar && <Avatar email={addr} name={name} photo={row?.photo} hue={row?.hue} size={size} />}
      <span className="person-name">{label}</span>
    </span>
  );
}

// ---- the picker (2026-09-06) ----------------------------------------------------------

export interface PickedPerson { email: string; name: string; job_title?: string }

/**
 * Type-ahead over the people directory (GET /api/people?q=): the hiring-manager
 * field on New requisition / Overview and the @-mention roster share it. Local
 * results come from everyone who has signed in or been named; Graph results
 * appear when the deployment has enabled it. A picked person shows as a chip with
 * the address READ-ONLY — a typed address is what the portal reaches them through,
 * so it is never editable once chosen. "Enter manually" stays available because
 * the directory is complete only once Graph is consented.
 */
export function PeoplePicker({ value, onChange, placeholder = "Search by name…", disabled = false, ariaLabel = "Search people", allowManual = true, autoFocus = false, manualLink = false }: {
  value: PickedPerson | null;
  onChange: (p: PickedPerson | null) => void;
  placeholder?: string;
  disabled?: boolean;
  ariaLabel?: string;
  allowManual?: boolean;
  autoFocus?: boolean;
  /** Show the "enter manually" link under the box at all times (not only inside the
   *  results menu) — for the forms where a typed name is the common path today. */
  manualLink?: boolean;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<PersonRow[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [manual, setManual] = useState(false);
  const [mName, setMName] = useState("");
  const [mEmail, setMEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  // Debounced search — every keystroke past the first waits 180 ms so a fast
  // typist costs one request, not one per letter.
  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setHits([]); setNote(null); return; }
    const mine = ++seq.current;
    setBusy(true);
    const t = window.setTimeout(() => {
      get<PeopleSearch>(`/api/people?q=${encodeURIComponent(term)}`)
        .then((r) => { if (seq.current === mine) { setHits(r.people ?? []); setNote(r.note ?? null); setActive(0); } })
        .catch(() => { if (seq.current === mine) { setHits([]); setNote(null); } })
        .finally(() => { if (seq.current === mine) setBusy(false); });
    }, 180);
    return () => window.clearTimeout(t);
  }, [q]);

  const pick = (p: PersonRow) => {
    onChange({ email: p.email, name: p.name || p.email, job_title: p.job_title || undefined });
    setQ(""); setHits([]); setOpen(false); setManual(false);
  };
  const looksLikeEmail = /\S+@\S+\.\S+/.test(q.trim());

  if (value) {
    return (
      <div className="picked-person" data-testid="picked-person">
        <Avatar email={value.email} name={value.name} size="sm" />
        <span style={{ minWidth: 0, display: "flex", flexDirection: "column", lineHeight: 1.25 }}>
          <span style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink)" }}>{value.name}</span>
          <span style={{ fontSize: 12, color: "var(--ink-soft)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                title={value.email}>
            {value.email}{value.job_title ? ` · ${value.job_title}` : ""}
          </span>
        </span>
        {!disabled && (
          <button type="button" onClick={() => onChange(null)} aria-label="Change person" className="picked-clear">
            Change
          </button>
        )}
      </div>
    );
  }

  if (manual) {
    return (
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <input className="input" style={{ flex: "1 1 150px", height: 40 }} placeholder="Name" aria-label="Name" value={mName}
               onChange={(e) => setMName(e.target.value)} autoComplete="off" />
        <input className="input" type="email" style={{ flex: "1 1 200px", height: 40 }} placeholder="email@example.com" aria-label="Email" value={mEmail}
               onChange={(e) => setMEmail(e.target.value)} autoComplete="off" />
        <button type="button" className="btn btn-secondary btn-sm"
                disabled={!mName.trim() || !/\S+@\S+\.\S+/.test(mEmail.trim())}
                onClick={() => onChange({ email: mEmail.trim(), name: mName.trim() })}>
          Use this person
        </button>
        <button type="button" className="link-quiet" onClick={() => setManual(false)}>Back to search</button>
      </div>
    );
  }

  return (
    <div ref={box} style={{ position: "relative" }}>
      <input
        className="input"
        style={{ height: 40 }}
        value={q}
        disabled={disabled}
        autoFocus={autoFocus}
        aria-label={ariaLabel}
        aria-expanded={open && hits.length > 0}
        autoComplete="off"
        placeholder={placeholder}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (!hits.length) {
            if (e.key === "Enter" && looksLikeEmail && allowManual) {
              e.preventDefault();
              onChange({ email: q.trim(), name: humanizeEmail(q.trim()) || q.trim() });
            }
            return;
          }
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => (i + 1) % hits.length); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => (i - 1 + hits.length) % hits.length); }
          else if (e.key === "Enter") { e.preventDefault(); pick(hits[Math.min(active, hits.length - 1)]); }
          else if (e.key === "Escape") { setOpen(false); }
        }}
      />
      {manualLink && allowManual && (
        <button type="button" className="link-quiet" style={{ marginTop: 6 }} onClick={() => { setManual(true); setOpen(false); }}>
          Enter a name and email manually
        </button>
      )}
      {open && q.trim().length >= 2 && (
        <div className="people-menu" role="listbox" aria-label="People">
          {hits.map((p, i) => (
            <button key={p.email} type="button" role="option" aria-selected={i === active}
                    className={"people-option" + (i === active ? " is-active" : "")}
                    onMouseDown={(e) => { e.preventDefault(); pick(p); }}
                    onMouseEnter={() => setActive(i)}>
              <Avatar email={p.email} name={p.name} photo={p.photo} hue={p.hue} size="sm" />
              <span style={{ minWidth: 0, display: "flex", flexDirection: "column", lineHeight: 1.25, textAlign: "left" }}>
                <span style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>{p.name || p.email}</span>
                <span style={{ fontSize: 11.5, color: "var(--ink-soft)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {p.job_title ? `${p.job_title} · ` : ""}{p.email}
                </span>
              </span>
            </button>
          ))}
          {!busy && hits.length === 0 && (
            <div className="people-empty">
              {looksLikeEmail
                ? <>No one by that address yet — press <b>Enter</b> to use it as typed.</>
                : <>No match{note ? ` — ${note}` : " in the people this app knows."}</>}
            </div>
          )}
          {busy && <div className="people-empty">Searching…</div>}
          {allowManual && (
            <button type="button" className="people-manual" onMouseDown={(e) => { e.preventDefault(); setManual(true); setOpen(false); }}>
              Enter a name and email manually
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** Convenience for prose: "Rahul suggested…" — a hook returning the resolver bound
 *  to the loaded directory. */
export function usePersonName(): (email: string | null | undefined, known?: string | null) => string {
  const people = usePeople();
  return (email, known) => personName(email, people, known);
}

export type { PersonRow };
export function PersonList({ children }: { children: ReactNode }) {
  return <span className="person-list">{children}</span>;
}
