"""The Resume Screener front door — the one page an unauthenticated visitor sees.

Server-rendered, deliberately: it is the response to a request that carries NO
identity, so it must not depend on the SPA bundle booting or on any API the visitor
cannot call. It is also the same page in both hosted modes, because the button target
``/.auth/login/aad`` is Easy Auth's login endpoint in ``prod`` AND this app's own
endpoint in ``uat`` (``entra_sso.py``) — one page, one href, no per-mode fork.

Why a page at all, rather than bouncing straight to Microsoft:

* signing out was impossible without it — ``/.auth/logout`` dropped the session,
  redirected to "/", and the bounce signed the same person back in before they saw
  anything;
* a person on the wrong account needs somewhere to stop and choose another;
* a bare Microsoft redirect never tells the visitor WHAT they are signing in to. If
  the link came from a colleague, "Resume-Screener-Dev" on a Microsoft error page is
  the first and only name they ever see.

Styling lives in ``web/public/edge.css``, shared with the two React edge screens
(no application access / please sign in again) so all three are recognisably one
product. Only the two brand assets and that stylesheet are fetched, all from our own
origin and all reachable before sign-in (the SPA catch-all serves real files ahead of
its auth branch).

Not to be confused with the *no-access* screen: that one is for a visitor who signed
in successfully but holds no app role, is rendered by the SPA (they are authenticated
by then), and lives in ``web/src/layout/AppShell.tsx``.
"""

from __future__ import annotations

import urllib.parse

# The four Microsoft squares. Rendered inline rather than fetched so the button never
# appears without its mark, and repeated in AppShell.tsx for the React edge screens —
# the two "Sign in with Microsoft" buttons must be recognisably one action.
_MS_SQUARES = (
    '<svg width="17" height="17" viewBox="0 0 23 23" aria-hidden="true">'
    '<rect width="10" height="10" x="1" y="1" fill="#f25022"/>'
    '<rect width="10" height="10" x="12" y="1" fill="#7fba00"/>'
    '<rect width="10" height="10" x="1" y="12" fill="#00a4ef"/>'
    '<rect width="10" height="10" x="12" y="12" fill="#ffb900"/>'
    "</svg>"
)

_TICK = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<polyline points="20 6 9 17 4 12"/></svg>'
)

# What the app does, in the recruiter's language (CLAUDE.md §7: no layer numbers,
# signal ids or weights on screen). Three, because a door is not documentation.
_POINTS = [
    ("Every score explained",
     "each requirement marked against the résumé, with the quote it came from"),
    ("A fairness check on every judgement",
     "off-limits reasoning is set aside before it can affect a score"),
    ("Your decision, recorded",
     "the tool ranks and explains; a person decides, and that is what is kept"),
]

_HTML = """<!doctype html>
<html lang="en" class="edge-doc"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="icon" type="image/svg+xml" href="/app-icon.svg">
<link rel="stylesheet" href="/edge.css">
</head>
<body class="edge-doc">
<main class="edge">
  <section class="edge-brand">
    <img class="edge-logo" src="/acme-logo-white.svg" alt="Acme">
    <div class="edge-brand-body">
      <p class="edge-eyebrow">Talent Acquisition</p>
      <h1 class="edge-title">Resume Screener</h1>
      <p class="edge-lede">{lede}</p>
      <ul class="edge-points">{points}</ul>
      <p class="edge-foot">Decision-support only. Scores and shortlists are for
        internal evaluation while this pilot runs, and never progress or reject a
        candidate on their own.</p>
    </div>
  </section>

  <section class="edge-action">
    <div class="edge-card">
      <img class="edge-mark" src="/app-icon.svg" alt="">
      <h2 class="edge-h">{heading}</h2>
      <p class="edge-p">{blurb}</p>
      <a class="edge-btn" href="/.auth/login/aad?post_login_redirect_uri={target}">
        {ms}Sign in with Microsoft
      </a>
      <p class="edge-hint">{hint}</p>
    </div>
  </section>
</main>
</body></html>
"""


def safe_next(next_url: str | None) -> str:
    """`next_url` reduced to a same-origin path, defaulting to "/".

    This value ends up in a redirect after sign-in, so an absolute URL here would
    be an open redirect: /.auth/signin?next=https://elsewhere.example would render
    a Acme-branded page whose button sends the visitor off-origin once Microsoft
    hands them back. Only a path is ever accepted — and "//host" is rejected too,
    since a protocol-relative URL is absolute in a browser despite starting "/".
    """
    candidate = (next_url or "").strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return "/"
    # A backslash is treated as a path separator by some browsers, so "/\evil.example"
    # can escape the origin the same way "//" does.
    if candidate.startswith("/\\"):
        return "/"
    return candidate


def render(*, next_url: str = "/", signed_out: bool = False) -> str:
    """The sign-in page HTML.

    `next_url` is where to land after Microsoft returns — carried through so a
    deep link survives the round trip, and reduced to a path by `safe_next`.
    `signed_out` switches the copy from "sign in" to "you've been signed out",
    which is what makes /.auth/logout terminate somewhere instead of bouncing
    straight back in.
    """
    # safe="/" and nothing else: this is a VALUE inside the login endpoint's query
    # string, so "?", "=" and especially "&" must be percent-encoded or the target's
    # own query string is parsed as further parameters of the login URL and lost
    # ("/reqs/x?tab=a&focus=b" arrived as "/reqs/x?tab=a").
    target = urllib.parse.quote(safe_next(next_url), safe="/")
    points = "".join(
        f"<li>{_TICK}<span><b>{title}</b> &mdash; {detail}</span></li>"
        for title, detail in _POINTS
    )
    if signed_out:
        heading = "You&#8217;ve been signed out"
        blurb = "You can close this tab, or sign back in below."
        hint = ("Your Microsoft session may still be active on this device. "
                "To switch account, sign in again and choose a different one.")
    else:
        heading = "Sign in"
        blurb = "Use your Acme account to continue."
        hint = ("<strong>No access yet?</strong> Sign in first &mdash; the app will "
                "tell you what to send your administrator.")
    return _HTML.format(
        title=("Signed out · Resume Screener" if signed_out
               else "Sign in · Resume Screener"),
        lede=("Screening support for Talent Acquisition: ranked, explainable "
              "shortlists built from the requirements you set."),
        points=points, heading=heading, blurb=blurb, hint=hint,
        ms=_MS_SQUARES, target=target,
    )
