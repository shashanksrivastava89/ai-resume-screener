import { Suspense, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useMe } from "../MeContext";
import { readDevIdentity, writeDevIdentity } from "../api";
import { Avatar } from "../people";
import { LeaveGuardProvider, pendingLeaveMessage, useConfirmLeave } from "../leave";
import { currentTheme, toggleTheme } from "../theme";
import { Button, CenterLoading } from "../components/ui";
import RegionPicker from "../components/RegionPicker";
import NotificationsBell from "../components/NotificationsBell";
import {
  IconRoles,
  IconGlobe,
  IconAccess,
  IconInfo,
  IconLock,
  IconUnlocked,
  IconFlag,
  IconFile,
  IconPrefsUser,
  IconClockAudit,
  IconApprovals,
  IconSliders,
} from "../components/icons";

type Hue = "green" | "violet" | "blue" | "teal" | "amber" | "orange" | "rose" | "slate";

/** One sidebar destination. `hue` is the item's colour (tokens.css semantic hues):
 *  the icon sits in a tinted tile and the active row takes the same tint, so the
 *  sidebar can be scanned by colour the way B2C settings screens are. */
function Item({ to, icon, hue = "green", children }:
  { to: string; icon: ReactNode; hue?: Hue; children: ReactNode }) {
  const nav = useNavigate();
  const confirmLeave = useConfirmLeave();
  return (
    <NavLink
      to={to}
      className={({ isActive }) => `nav-link nav-hue-${hue}` + (isActive ? " active" : "")}
      onClick={(e) => {
        // An editor with unpublished changes gets to say so before the page changes
        // (leave.tsx). Clean editors never see this branch.
        if (pendingLeaveMessage()) { e.preventDefault(); confirmLeave(() => nav(to)); }
      }}
    >
      <span className="nav-icon" aria-hidden="true">{icon}</span>
      {children}
    </NavLink>
  );
}

// Small count pill for a nav item (the approval-queue badge).
function NavBadge({ count }: { count: number }) {
  if (!count) return null;
  return (
    <span style={{ marginLeft: "auto", fontSize: 11, fontWeight: 700, minWidth: 18, textAlign: "center", padding: "1px 6px", borderRadius: 999, background: "var(--hue-amber-solid)", color: "#fff" }}>
      {count}
    </span>
  );
}

// Role name + what it gets you, kept apart so a person holding SEVERAL roles can be
// shown as all of them. `me.role` is a single "display role" — the most-capable one —
// which is arbitrary between TA Specialist and HR Specialist because those two have
// IDENTICAL capabilities, so it just showed whichever came first in rbac.ROLES. A
// recruiter holding both saw only "TA Specialist" and reasonably read that as her HR
// Specialist role having been lost.
const ROLE_NAME: Record<string, string> = {
  SuperAdmin: "Super Admin",
  HRHead: "HR Head",
  Legal: "Legal",
  IT: "IT",
  TASpecialist: "TA Specialist",
  HRSpecialist: "HR Specialist",
  HiringManager: "Hiring Manager",
};

const ROLE_SCOPE: Record<string, string> = {
  SuperAdmin: "everything",
  HRHead: "all reqs + approvals",
  Legal: "approvals & audit",
  IT: "access off (for now)",
  TASpecialist: "own reqs",
  HRSpecialist: "own reqs",
  HiringManager: "review takes",
};

type Identity = { user?: string; name?: string; job_title?: string; photo?: string | null;
                  avatar_hue?: string; role?: string; roles?: string[] } | null;

/** Every role held, deterministically ordered. This is now HOVER text, not the
 *  visible label — see identityTitle. */
function roleLabel(me: Identity): string {
  const held = (me?.roles?.length ? me.roles : me?.role ? [me.role] : [])
    .filter((r) => r in ROLE_NAME);
  if (!held.length) return me?.role ?? "";
  const order = Object.keys(ROLE_NAME);
  return [...held].sort((a, b) => order.indexOf(a) - order.indexOf(b))
    .map((r) => ROLE_NAME[r]).join(" + ");
}

/** The line under the person's name.
 *
 *  Recruiters don't think in app roles ("TA Specialist" is our word, not theirs), and
 *  a role someone holds today can be re-mapped tomorrow — so the ASK was to show the
 *  directory job title, which is always current. `jobTitle` is not a token claim
 *  though, so the app cannot see it without a claims-mapping policy or Graph
 *  User.Read.All + admin consent. Until that exists this shows what the person can
 *  DO ("own reqs", "everything") — access, not role jargon — and the role names move
 *  to hover. `job_title` is the seam: the moment /api/me can fill it, it wins here
 *  and nothing else changes. */
function identityTitle(me: Identity): string {
  if (me?.job_title?.trim()) return me.job_title.trim();
  return ROLE_SCOPE[me?.role ?? ""] ?? roleLabel(me);
}

/** The name to call someone: the SSO `name` claim, else their sign-in address. Acme
 *  UPNs are payroll ids, so the address is a poor label — but it beats guessing. */
function identityName(me: Identity): string {
  return me?.name?.trim() || me?.user || "";
}

// The seven app roles, short form, for the dev switcher's checklist.
const ROLE_SHORT: [string, string][] = [
  ["SuperAdmin", "Super Admin"],
  ["HRHead", "HR Head"],
  ["Legal", "Legal"],
  ["IT", "IT (off for now)"],
  ["TASpecialist", "TA Specialist"],
  ["HRSpecialist", "HR Specialist"],
  ["HiringManager", "Hiring Manager"],
];

// The v3 design's five demo personas — one-click identities for the dev
// switcher (SSO principals hide all of this).
const PERSONAS: { label: string; user: string; roles: string[] }[] = [
  { label: "Arjun · Admin", user: "arjun.test@example.com", roles: ["SuperAdmin"] },
  { label: "Bhavika · TA", user: "bhavika.test@example.com", roles: ["TASpecialist"] },
  { label: "Meera · HR", user: "meera.test@example.com", roles: ["HRSpecialist"] },
  { label: "HR Head", user: "hrhead.test@example.com", roles: ["HRHead"] },
  { label: "Leena · Legal", user: "leena.test@example.com", roles: ["Legal"] },
  { label: "Rahul · HM", user: "gerard.test@example.com", roles: ["HiringManager"] },
];

// The design's persona chip row: flex-wrapped small chips, active = green tint.
function PersonaChips({ currentUser }: { currentUser: string }) {
  const pick = (p: (typeof PERSONAS)[number]) => {
    writeDevIdentity({ user: p.user, roles: p.roles });
    window.location.reload();
  };
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 10 }}>
      {PERSONAS.map((p) => {
        const on = p.user.toLowerCase() === currentUser.toLowerCase();
        return (
          <button
            key={p.user}
            onClick={() => pick(p)}
            style={{
              flex: 1, minWidth: 70, height: 26, borderRadius: 8,
              fontFamily: "inherit", fontSize: 10, fontWeight: 700, cursor: "pointer",
              border: `1px solid ${on ? "var(--brand-green)" : "var(--border-subtle)"}`,
              background: on ? "var(--brand-green-tint)" : "var(--white)",
              color: on ? "var(--brand-green-deep)" : "var(--ink-soft)",
              whiteSpace: "nowrap",
            }}
          >
            {p.label}
          </button>
        );
      })}
    </div>
  );
}

// Dev-only user switcher (shown when /api/me says dev_switch — i.e. no SSO
// principal): change who you are WITHOUT an app restart, so demos can flip
// between a TA screening and an HR Head reviewing while a run stays live.
// The choice persists in localStorage and rides every request as headers.
function DevUserSwitcher({ currentUser }: { currentUser: string }) {
  const saved = readDevIdentity();
  const [open, setOpen] = useState(false);
  const [user, setUser] = useState(saved?.user ?? currentUser);
  const [roles, setRoles] = useState<string[]>(saved?.roles ?? []);

  const toggleRole = (role: string) =>
    setRoles((r) => (r.includes(role) ? r.filter((x) => x !== role) : [...r, role]));

  const apply = () => {
    writeDevIdentity({ user: user.trim() || undefined, roles: roles.length ? roles : undefined });
    window.location.reload(); // fresh /api/me + caches under the new identity
  };
  const reset = () => {
    writeDevIdentity(null);
    window.location.reload();
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        style={{ display: "block", width: "100%", marginBottom: 10, padding: "4px 2px", border: 0, background: "none", color: "var(--ink-muted)", fontFamily: "inherit", fontSize: 10.5, fontWeight: 700, cursor: "pointer", textAlign: "left" }}
      >
        More identities…{saved ? " (custom on)" : ""}
      </button>
    );
  }
  return (
    <div style={{ marginBottom: 10, padding: "10px 12px", border: "1px dashed var(--border-strong)", borderRadius: 10 }}>
      <div style={{ fontSize: 11.5, fontWeight: 700, color: "var(--ink-soft)", marginBottom: 8 }}>
        Act as (until sign-in is connected)
      </div>
      <input
        className="input"
        style={{ height: 30, fontSize: 12, marginBottom: 8 }}
        placeholder="name or email"
        value={user}
        onChange={(e) => setUser(e.target.value)}
      />
      <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 10 }}>
        {ROLE_SHORT.map(([key, label]) => (
          <label key={key} style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12, color: "var(--ink)", cursor: "pointer" }}>
            <input type="checkbox" checked={roles.includes(key)} onChange={() => toggleRole(key)} />
            {label}
          </label>
        ))}
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <Button size="sm" onClick={apply} disabled={!roles.length && !user.trim()}>Apply</Button>
        <Button size="sm" variant="ghost" onClick={reset}>Reset</Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Close</Button>
      </div>
    </div>
  );
}

// Shown when the signed-in user has no (known) app role yet (P27): friendly,
// names the seven roles, and tells them who to ask. The API returns the same
// message as a 403 on every route, so this is presentation, not the gate.
/** The text an administrator needs, ready to paste into a ticket or a chat. The app's
 *  own origin identifies the deployment unambiguously — a name like
 *  "Resume-Screener-Dev" is environment-specific and would be wrong half the time. */
function accessRequestText(user: string): string {
  return [
    "Resume Screener — access request",
    `App:              ${window.location.origin}`,
    `Sign-in address:  ${user}`,
    "Role needed:      (Super Admin / HR Head / Legal / TA Specialist / HR Specialist / Hiring Manager)",
    "",
    "Microsoft sign-in already succeeds; no Resume Screener role is assigned.",
    "To add: Entra admin centre -> Enterprise applications -> Resume Screener",
    "        -> Users and groups -> Add user/group -> pick the role above.",
  ].join("\n");
}

/** Copy, and SAY what happened. navigator.clipboard.writeText throws
 *  NotAllowedError whenever the page is not a secure context or the browser policy
 *  forbids it (measured: "Write permission denied"), and the original silent catch
 *  turned this into a button that visibly did nothing — on the one screen whose whole
 *  purpose is getting these details to an administrator. On failure the caller shows
 *  the text instead, so there is always a way to get it. */
async function copyAccessRequest(user: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(accessRequestText(user));
    return true;
  } catch {
    return false;
  }
}

/** The brand half of an edge screen. Shared by both React edge screens and matched,
 *  markup for markup, by the server-rendered front door — one product, whichever of
 *  the three a person happens to land on. Styling: web/public/edge.css. */
function EdgeBrand() {
  return (
    <section className="edge-brand">
      <img className="edge-logo" src="/acme-logo-white.svg" alt="Acme" />
      <div className="edge-brand-body">
        <p className="edge-eyebrow">Talent Acquisition</p>
        <h1 className="edge-title">Resume Screener</h1>
        <p className="edge-lede">
          Screening support for Talent Acquisition: ranked, explainable shortlists
          built from the requirements you set.
        </p>
        <p className="edge-foot">
          Decision-support only. Scores and shortlists are for internal evaluation
          while this pilot runs, and never progress or reject a candidate on their own.
        </p>
      </div>
    </section>
  );
}

/** The same four Microsoft squares as the server-rendered door, so both
 *  "Sign in with Microsoft" buttons are recognisably one action. */
function MicrosoftSquares() {
  return (
    <svg width="17" height="17" viewBox="0 0 23 23" aria-hidden="true">
      <rect width="10" height="10" x="1" y="1" fill="#f25022" />
      <rect width="10" height="10" x="12" y="1" fill="#7fba00" />
      <rect width="10" height="10" x="1" y="12" fill="#00a4ef" />
      <rect width="10" height="10" x="12" y="12" fill="#ffb900" />
    </svg>
  );
}

const ASSIGNABLE_ROLES: [string, string][] = [
  ["Super Admin", "everything, from every user"],
  ["HR Head", "all requisitions, preference & scoring approvals"],
  ["Legal", "approvals, audit, scoring model and jurisdictions"],
  ["TA Specialist", "their own requisitions and preferences"],
  ["HR Specialist", "their own requisitions and preferences"],
  ["Hiring Manager", "reviewing and recording takes on assigned roles"],
];

function AccessNotConfigured({ user }: { user: string }) {
  const [copied, setCopied] = useState<"idle" | "yes" | "no">("idle");
  return (
    <main className="edge">
      <EdgeBrand />
      <section className="edge-action">
        <div className="edge-card is-wide">
          <img className="edge-mark" src="/app-icon.svg" alt="" />
          <h2 className="edge-h">You do not have access to this application</h2>
          <p className="edge-p">
            Your Microsoft sign-in worked — but no Resume Screener role is assigned to
            you yet, so there is nothing here you can open.
          </p>
          <span className="edge-account">{user}</span>
          {/* The action, stated before the reference list. Someone who lands here needs
              to know WHO to ask, not to read six role definitions first. */}
          <p className="edge-note">
            <strong>Contact your administrator</strong> to be added. Send them the
            request below — it names the app, this account, and where to add you.
          </p>
          {/* Microsoft's own blocked-access page offers "copy info to clipboard", and it
              is the one genuinely useful thing on it — except what it copies is a
              correlation id no administrator can act on. */}
          <div className="edge-actions">
            <button
              type="button"
              className="edge-btn-2"
              onClick={async () => setCopied(await copyAccessRequest(user) ? "yes" : "no")}
            >
              {copied === "yes" ? "Copied" : "Copy request for your administrator"}
            </button>
            <a className="edge-btn-2" href={SIGN_OUT_HREF}>Sign out</a>
            {/* A demo identity with no valid role would otherwise strand the user here. */}
            {readDevIdentity() && (
              <button type="button" className="edge-btn-2"
                      onClick={() => { writeDevIdentity(null); window.location.reload(); }}>
                Reset demo user
              </button>
            )}
          </div>
          {copied === "no" && (
            <textarea
              readOnly
              className="edge-pre"
              aria-label="Request for your administrator"
              value={accessRequestText(user)}
              onFocus={(e) => e.currentTarget.select()}
            />
          )}
          <div className="edge-roles">
            {ASSIGNABLE_ROLES.map(([role, what]) => (
              <div key={role}><b>{role}</b> — {what}</div>
            ))}
          </div>
        </div>
      </section>
    </main>
  );
}

// Sign out. ONE href for both hosted paths: /.auth/logout is Easy Auth's endpoint in
// prod and our own route in uat, so the button doesn't need to know which is serving it.
// It carries a post-logout target so the redirect TERMINATES on the signed-out page
// rather than bouncing back through sign-in and returning the person to the app they
// just left. `?signed_out=1` is what switches that page's copy.
export const SIGN_OUT_HREF =
  "/.auth/logout?post_logout_redirect_uri=" + encodeURIComponent("/?signed_out=1");

// Hidden in dev mode, where there is no sign-in to undo — the persona switcher is the
// equivalent there, and offering "sign out" would be a button that does nothing.
function SignOut({ mode }: { mode?: string }) {
  if (mode === "dev") return null;
  return (
    <a
      href={SIGN_OUT_HREF}
      style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, padding: "6px 10px", border: "1px solid var(--border-subtle)", borderRadius: 8, background: "var(--white)", color: "var(--ink-soft)", fontSize: 11.5, fontWeight: 700, textDecoration: "none" }}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" />
      </svg>
      Sign out
    </a>
  );
}

// The session went away while the app was open (token lifetime, or a sign-out in
// another tab). Distinct from AccessNotConfigured on purpose: telling someone "you do
// not have access to this application" when their token merely expired sends them to
// their administrator for nothing. The fix here is one click, and the deep link they
// were on is preserved through the round trip.
function SessionExpired() {
  // The ROUTER's location, not window.location: it is the source of truth for where
  // the person is, and reading the window couples this to the history adapter.
  const loc = useLocation();
  const back = loc.pathname + loc.search;
  return (
    <main className="edge">
      <EdgeBrand />
      <section className="edge-action">
        <div className="edge-card">
          <img className="edge-mark" src="/app-icon.svg" alt="" />
          <h2 className="edge-h">Please sign in again</h2>
          <p className="edge-p">
            Your Resume Screener session has ended. Nothing was lost — signing in
            brings you back to where you were.
          </p>
          <a className="edge-btn"
             href={"/.auth/login/aad?post_login_redirect_uri=" + encodeURIComponent(back)}>
            <MicrosoftSquares />
            Sign in with Microsoft
          </a>
          <p className="edge-hint">
            Sessions end after a period of inactivity, or when you sign out in another
            tab. This is not an access problem.
          </p>
        </div>
      </section>
    </main>
  );
}

// Dark-mode toggle (2026-07-19): flips the data-theme attribute the token
// overrides key off; the choice persists per browser (web/src/theme.ts).
function ThemeToggle() {
  const [theme, setTheme] = useState(currentTheme());
  return (
    <button
      onClick={() => setTheme(toggleTheme())}
      aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", marginBottom: 10, padding: "6px 10px", border: "1px solid var(--border-subtle)", borderRadius: 8, background: "var(--white)", color: "var(--ink-soft)", fontFamily: "inherit", fontSize: 11.5, fontWeight: 700, cursor: "pointer" }}
    >
      {theme === "dark" ? (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z" /></svg>
      )}
      {theme === "dark" ? "Light mode" : "Dark mode"}
    </button>
  );
}

export default function AppShell() {
  const me = useMe();
  const admin = me?.has_admin ?? false;
  const caps = me?.capabilities ?? [];

  if (me && me.authenticated === false) return <SessionExpired />;
  if (me && !me.known_role) return <AccessNotConfigured user={me.user} />;

  return (
    <LeaveGuardProvider>
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <aside
        style={{
          flex: "0 0 240px",
          background: "var(--white)",
          borderRight: "1px solid var(--border-subtle)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* The logo routes home (2026-07-20): "/" lands each role on their first
            page — recruiters/HM → Requisitions, Legal → Approvals, etc. (App's
            Home()) — so a user without the Requisitions tab never dead-ends. */}
        <NavLink to="/" aria-label="Home" style={{ display: "block", padding: "20px 20px 14px", textDecoration: "none" }}>
          <img src="/acme-logo.svg" alt="Acme" style={{ height: 24, display: "block", marginBottom: 14 }} />
          <div style={{ fontSize: 14, fontWeight: 700, color: "var(--ink)" }}>Resume Screener</div>
          <div style={{ fontSize: 11.5, color: "var(--ink-soft)", marginTop: 2 }}>Talent Acquisition</div>
        </NavLink>

        <nav
          style={{
            flex: "1 1 auto",
            padding: "4px 12px",
            display: "flex",
            flexDirection: "column",
            gap: 3,
            overflowY: "auto",
          }}
        >
          {(caps.includes("recruiter") || caps.includes("hm_take") || caps.includes("reqs_read")) && (
            <Item to="/reqs" hue="green" icon={<IconRoles size={17} />}>Requisitions</Item>
          )}
          {caps.includes("prefs_own") && <Item to="/preferences" hue="violet" icon={<IconPrefsUser size={17} />}>My preferences</Item>}

          {admin && (
            <>
              <div
                style={{
                  fontSize: 10.5,
                  fontWeight: 700,
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  color: "var(--ink-muted)",
                  padding: "16px 12px 6px",
                }}
              >
                Governance
              </div>
              {caps.includes("runs_audit") && <Item to="/audit" hue="blue" icon={<IconClockAudit size={17} />}>Audit &amp; History</Item>}
              {(caps.includes("prefs_approve") || caps.includes("changes_approve")) && (
                <Item to="/approvals" hue="amber" icon={<IconApprovals size={17} />}>
                  Approvals
                  <NavBadge count={(me?.pref_pending ?? 0) + (me?.changes_pending ?? 0)} />
                </Item>
              )}
              {caps.includes("rules_view") && <Item to="/scoring" hue="teal" icon={<IconSliders size={17} />}>Scoring model</Item>}
              {caps.includes("rules_view") && <Item to="/jurisdictions" hue="blue" icon={<IconGlobe size={17} />}>Jurisdictions</Item>}
              {caps.includes("access_view") && <Item to="/access" hue="rose" icon={<IconAccess size={17} />}>Access &amp; Roles</Item>}
              {caps.includes("flags_view") && <Item to="/flags" hue="orange" icon={<IconFlag size={17} />}>Feature flags</Item>}
              {caps.includes("data_manage") && <Item to="/data" hue="slate" icon={<IconFile size={17} />}>Data</Item>}
            </>
          )}
        </nav>

        <div style={{ padding: "14px 16px", borderTop: "1px solid var(--border-subtle)" }}>
          <RegionPicker />
          <NotificationsBell />
          <ThemeToggle />
          {/* The identity block (2026-09-06): the directory photo when there is one,
              else initials on the person's own stable hue — the same avatar this
              person wears on every other surface. The line under the name is the
              directory job title the moment it is known (Graph), else what the
              person can DO here. */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
            <Avatar email={me?.user ?? ""} name={identityName(me)} photo={me?.photo} hue={me?.avatar_hue} size="md" />
            <div style={{ minWidth: 0 }}>
              <div
                title={me?.user ?? ""}
                style={{
                  fontSize: 12.5,
                  fontWeight: 700,
                  color: "var(--ink)",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {identityName(me) || "…"}
              </div>
              <div
                title={me?.job_title?.trim() ? `${roleLabel(me)} · ${ROLE_SCOPE[me?.role ?? ""] ?? ""}` : roleLabel(me)}
                style={{ fontSize: 10.5, color: "var(--ink-soft)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
              >
                {identityTitle(me)}
              </div>
            </div>
          </div>
          {me?.dev_switch && (
            <>
              <PersonaChips currentUser={me.user} />
              <DevUserSwitcher currentUser={me.user} />
            </>
          )}
          <SignOut mode={me?.mode?.mode} />
          <div style={{ fontSize: 10.5, lineHeight: 1.5, color: "var(--ink-soft)" }}>
            It suggests — <strong style={{ color: "var(--ink)" }}>you</strong> decide. No one is
            rejected automatically.
          </div>
        </div>
      </aside>

      <main
        style={{
          flex: "1 1 auto",
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
          height: "100vh",
          position: "relative",
        }}
      >
        {/* Entry-mode badge (2026-08-20). prod carries no label on purpose — the real
            thing needs no sticker, and an always-on banner there would train people to
            ignore banners. dev and uat DO, because mistaking one for production is the
            expensive mistake. */}
        {/* ONE status strip (2026-09-05). These were three stacked bars — mode, evaluation
            mode, draft configuration — ~75px of notices before any content on every
            page. Same three facts, one line: each notice keeps its full text on hover,
            and nothing here is hidden behind a click. */}
        {(me?.mode?.label || me?.evaluation_mode || (me?.review_pending && admin)) && (
          <div className="status-strip">
            {me?.mode?.label && (
              <span
                className={"chip " + (me.mode.mode === "dev" ? "chip-quiet" : "chip-info")}
                title={me.mode.summary}
                style={{ flex: "0 0 auto" }}
              >
                {me.mode.mode === "dev" ? <IconUnlocked size={12} /> : <IconLock size={12} />}
                <span className="chip-label">{me.mode.label}</span>
              </span>
            )}
            {me?.evaluation_mode && (
              <span className="status-note status-note-warn"
                    title="Evaluation mode — outputs inform testing only, pending HR + Legal review. Nothing is used on live requisitions.">
                <IconInfo size={13} />
                <strong>Evaluation mode</strong>
                <span className="status-note-text"> — outputs inform testing only, pending HR + Legal review. Nothing is used on live requisitions.</span>
              </span>
            )}
            {me?.review_pending && admin && (
              <span className="status-note"
                    title="Draft configuration — pending HR + Legal review. Record the sign-off in Rules & Signals once reviewed.">
                <strong>Draft configuration</strong>
                <span className="status-note-text"> — pending HR + Legal review. Record the sign-off in Rules &amp; Signals once reviewed.</span>
              </span>
            )}
          </div>
        )}

        <div style={{ flex: "1 1 auto", minHeight: 0, overflow: "hidden" }}>
          {/* one Suspense boundary for every code-split (lazy) admin page */}
          <Suspense fallback={<CenterLoading label="Loading…" />}>
            <Outlet />
          </Suspense>
        </div>
      </main>
    </div>
    </LeaveGuardProvider>
  );
}

// Standard scrolling page wrapper used by most pages.
export function Page({ children, max = 1120 }: { children: ReactNode; max?: number }) {
  return (
    <div style={{ height: "100%", overflowY: "auto", padding: "34px 40px 60px" }}>
      <div style={{ maxWidth: max, margin: "0 auto" }} className="rise">
        {children}
      </div>
    </div>
  );
}
