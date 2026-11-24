"""Security headers on every response (InfoSec hardening, 2026-10-14).

Set once, in the request middleware (app._stash_request_caps), so a new route can never
forget them. What the browser is told:

- Content-Security-Policy, on HTML (the SPA shell and the sign-in page — where a
  policy protects anything): scripts, styles, fonts and fetches only from this
  origin; images also from `data:` (avatars and the select chevron are inlined); the
  résumé preview is an <object> of our own PDF route, so object and frame sources are
  'self'; and nothing may frame the app but the app itself. Not on PDFs, JSON or
  assets: a policy there protects nothing and can upset a browser's PDF viewer.
- X-Frame-Options: SAMEORIGIN — the same framing rule for browsers that predate
  `frame-ancestors`. SAMEORIGIN, not DENY: the résumé preview IS the app framing its
  own response, and DENY would blank it.
- X-Content-Type-Options: nosniff — a response is what its Content-Type says.
- Referrer-Policy: strict-origin-when-cross-origin — a deep link's path (which names
  a requisition) never travels in a Referer header to another site.
- Permissions-Policy — no camera, microphone or location, ever.
- Strict-Transport-Security — browsers keep to HTTPS for a year. Ignored over plain
  HTTP, so local development is unaffected.

CSP_REPORT_ONLY=1 sends the policy as Content-Security-Policy-Report-Only instead: the
browser reports in its console what it WOULD block and blocks nothing — the switch for
a first roll to a new environment.
"""

from __future__ import annotations

import os

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "object-src 'self'",
    "frame-src 'self'",
    "frame-ancestors 'self'",
    "base-uri 'self'",
    "form-action 'self'",
])

HEADERS = {
    "X-Frame-Options": "SAMEORIGIN",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}

_CSP_NAMES = ("Content-Security-Policy", "Content-Security-Policy-Report-Only")


def report_only() -> bool:
    return (os.environ.get("CSP_REPORT_ONLY") or "").strip().lower() in ("1", "true", "yes", "on")


def apply(response) -> None:
    """Add the headers a response does not already carry (a route that sets its own —
    the dev-only API docs page — keeps it)."""
    headers = response.headers
    for name, value in HEADERS.items():
        headers.setdefault(name, value)
    is_html = headers.get("content-type", "").lower().startswith("text/html")
    if is_html and not any(name in headers for name in _CSP_NAMES):
        headers[_CSP_NAMES[1] if report_only() else _CSP_NAMES[0]] = CSP
