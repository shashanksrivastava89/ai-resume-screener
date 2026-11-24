// Signed in to Microsoft, but not assigned a role — and the way out (2026-08-27).
//
// This is the FIRST screen a new user sees until IT assigns them a role, so it has to
// say what happened, who to ask, and give them a way off it. Most people who land here
// are simply on the wrong account, which is why signing out matters more than the role
// reference list.
import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mockApi, renderPage, DEFAULT_ME } from "./utils";
import AppShell from "../layout/AppShell";

function shell(me: Record<string, unknown>) {
  mockApi({ "/api/me": { ...DEFAULT_ME, ...me }, "/api/roles": { roles: [], metrics: {} } });
  renderPage(<AppShell />, { route: "/reqs" });
}

const NO_ROLE = { user: "new.joiner@example.com", role: "", roles: [], known_role: false,
                  capabilities: [], has_admin: false };

describe("No application access", () => {
  it("says so plainly, names the account, and points at the administrator", async () => {
    shell(NO_ROLE);
    expect(await screen.findByText("You do not have access to this application"))
      .toBeInTheDocument();
    // The account is stated: the usual cause is being on the wrong one.
    expect(screen.getByText("new.joiner@example.com")).toBeInTheDocument();
    expect(screen.getByText(/Contact your administrator/)).toBeInTheDocument();
    // It says the sign-in itself worked, so nobody retries their password.
    expect(screen.getByText(/Microsoft sign-in worked/)).toBeInTheDocument();
  });

  it("offers sign out — the one useful action from here", async () => {
    shell(NO_ROLE);
    const out = await screen.findByRole("link", { name: "Sign out" });
    // Carries a landing target: a bare /.auth/logout redirected to "/" and the
    // bounce signed the same person straight back into the app they just left.
    expect(out.getAttribute("href")).toContain("/.auth/logout");
    expect(out.getAttribute("href")).toContain("signed_out%3D1");
  });

  it("still lists the assignable roles, after the action", async () => {
    shell(NO_ROLE);
    expect(await screen.findByText("TA Specialist")).toBeInTheDocument();
    expect(screen.getByText("Hiring Manager")).toBeInTheDocument();
  });

  it("does not show the app to someone with no role", async () => {
    shell(NO_ROLE);
    expect(await screen.findByText("You do not have access to this application"))
      .toBeInTheDocument();
    expect(screen.queryByText("Requisitions")).not.toBeInTheDocument();
  });
});

describe("Sign out in the app", () => {
  it("is offered when a real sign-in is in play", async () => {
    shell({ mode: { mode: "uat", label: "UAT", summary: "", auth_required: true,
                    dev_switch: false, sso: "direct", hosted: true } });
    const out = await screen.findByRole("link", { name: "Sign out" });
    // ONE href for both hosted paths: Easy Auth's endpoint in prod, ours in uat.
    expect(out.getAttribute("href")).toContain("/.auth/logout");
  });

  it("is hidden in dev mode, where there is no sign-in to undo", async () => {
    shell({ mode: { mode: "dev", label: "DEV", summary: "", auth_required: false,
                    dev_switch: true, sso: "none", hosted: false } });
    expect(await screen.findByText("Requisitions")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Sign out" })).not.toBeInTheDocument();
  });
});

describe("The identity block", () => {
  it("shows the person's real name, not their payroll sign-in address", async () => {
    // Hosted UPNs are payroll ids, so the sidebar read "u115652@example.com".
    // Easy Auth sends a `name` claim and rbac already parsed it — nothing consumed it.
    shell({ user: "u115652@example.com", name: "Jasvinder Cremin",
            role: "HRSpecialist", roles: ["TASpecialist", "HRSpecialist"] });
    expect(await screen.findByText("Jasvinder Cremin")).toBeInTheDocument();
    expect(screen.queryByText("u115652@example.com")).not.toBeInTheDocument();
  });

  it("keeps the sign-in address on hover, for support", async () => {
    shell({ user: "u115652@example.com", name: "Jasvinder Cremin",
            role: "HRSpecialist" });
    expect(await screen.findByTitle("u115652@example.com")).toBeInTheDocument();
  });

  it("falls back to the address when there is no name claim", async () => {
    shell({ user: "u115652@example.com", name: "", role: "HRSpecialist" });
    expect(await screen.findByText("u115652@example.com")).toBeInTheDocument();
  });

  it("describes what the person can do, not which app role they hold", async () => {
    // The ask: show something always-current instead of internal role names. The
    // directory job title isn't a token claim, so this is the access scope.
    shell({ user: "jk@example.com", name: "Jasvinder Cremin", role: "HRSpecialist",
            roles: ["TASpecialist", "HRSpecialist"] });
    expect(await screen.findByText("own reqs")).toBeInTheDocument();
    expect(screen.queryByText(/TA Specialist/)).not.toBeInTheDocument();
  });

  it("takes the scope from the most-capable role held", async () => {
    // A Super Admin who also holds IT must not be described as "access off".
    shell({ user: "sa@example.com", name: "Arjun Test", role: "SuperAdmin",
            roles: ["IT", "SuperAdmin"] });
    expect(await screen.findByText("everything")).toBeInTheDocument();
  });

  it("still names every role held, on hover", async () => {
    // The roles didn't stop mattering — a recruiter who reported her HR Specialist
    // role looking lost must still be able to see both, just not as the label.
    shell({ user: "jk@example.com", name: "Jasvinder Cremin", role: "HRSpecialist",
            roles: ["TASpecialist", "HRSpecialist"] });
    expect(await screen.findByTitle("TA Specialist + HR Specialist")).toBeInTheDocument();
  });

  it("prefers the directory job title the moment the server can send one", async () => {
    // The seam for the real ask (Graph User.Read.All + admin consent). Server-side
    // fill is the entire remaining change; this pins that the client is ready.
    shell({ user: "jk@example.com", name: "Jasvinder Cremin", role: "HRSpecialist",
            job_title: "Senior Talent Acquisition Partner" });
    expect(await screen.findByText("Senior Talent Acquisition Partner")).toBeInTheDocument();
    expect(screen.queryByText("own reqs")).not.toBeInTheDocument();
  });
});

describe("An expired session is not a missing role", () => {
  it("asks the person to sign in again instead of sending them to an administrator", async () => {
    // Both cases arrive as known_role: false. Telling someone whose token merely
    // timed out that they "do not have access to this application" sends them to
    // IT for nothing; `authenticated` separates the two.
    shell({ ...NO_ROLE, user: "", authenticated: false });
    expect(await screen.findByText("Please sign in again")).toBeInTheDocument();
    expect(screen.queryByText("You do not have access to this application"))
      .not.toBeInTheDocument();
  });

  it("brings them back to the page they were on", async () => {
    shell({ ...NO_ROLE, user: "", authenticated: false });
    const link = await screen.findByRole("link", { name: /Sign in with Microsoft/ });
    expect(link.getAttribute("href")).toContain("post_login_redirect_uri=");
    expect(link.getAttribute("href")).toContain(encodeURIComponent("/reqs"));
  });

  it("still shows the no-access page for a real principal with no role", async () => {
    shell({ ...NO_ROLE, authenticated: true });
    expect(await screen.findByText("You do not have access to this application"))
      .toBeInTheDocument();
  });

  it("treats a payload without the field as signed in, so it can only ADD a screen", async () => {
    // Deploy ordering: a cached client must never decide the server logged you out.
    shell(NO_ROLE);
    expect(await screen.findByText("You do not have access to this application"))
      .toBeInTheDocument();
    expect(screen.queryByText("Please sign in again")).not.toBeInTheDocument();
  });
});

describe("Copy request for your administrator", () => {
  it("copies which app, which account, and where to add them", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText }, configurable: true, writable: true,
    });
    shell(NO_ROLE);
    await userEvent.click(await screen.findByRole("button",
      { name: "Copy request for your administrator" }));
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
    const text = writeText.mock.calls[0][0] as string;
    // Microsoft's page copies a correlation id no administrator can act on. This
    // copies the three facts that actually get someone added.
    expect(text).toContain("new.joiner@example.com");
    expect(text).toContain("Users and groups");
    expect(text).toContain("HR Specialist");
    expect(text).toContain(window.location.origin);
  });
});

describe("When the browser refuses the clipboard", () => {
  it("shows the text instead of silently doing nothing", async () => {
    // Measured in a real browser: writeText throws NotAllowedError ("Write
    // permission denied") outside a secure context or under browser policy. The
    // original silent catch made this button a no-op on the one screen whose only
    // purpose is handing these details to an administrator.
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockRejectedValue(new Error("NotAllowedError")) },
      configurable: true, writable: true,
    });
    shell(NO_ROLE);
    await userEvent.click(await screen.findByRole("button",
      { name: "Copy request for your administrator" }));
    const box = await screen.findByRole("textbox",
      { name: "Request for your administrator" }) as HTMLTextAreaElement;
    expect(box.value).toContain("new.joiner@example.com");
    expect(box.value).toContain("Users and groups");
    expect(box.readOnly).toBe(true);
  });
});
