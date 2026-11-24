"""The front door: the page an unauthenticated visitor gets (2026-09-03).

Until now a visitor with no session was redirected straight to Microsoft by Easy
Auth, so the first — and if they were unassigned, the ONLY — thing they ever saw
was a Microsoft error page naming "Resume-Screener-Dev". This page is ours, says
what the app is, and gives sign-out somewhere to land.
"""

from resume_screener.api import signin_page


def test_it_offers_microsoft_sign_in_and_says_what_the_app_is():
    html = signin_page.render()
    assert "Sign in with Microsoft" in html
    assert "/.auth/login/aad?post_login_redirect_uri=/" in html
    assert "Resume Screener" in html
    # The standing constraint (CLAUDE.md §0) is stated BEFORE anyone signs in.
    # Asserted on substance, not one phrasing, so copy edits don't fail this.
    assert "internal evaluation" in html
    assert "never progress or reject" in html


def test_it_carries_the_app_identity_and_the_shared_edge_styling():
    """The door, the no-access screen and the session screen share edge.css so the
    three read as one product; the mark doubles as the browser-tab icon, which the
    app had none of (a default globe)."""
    html = signin_page.render()
    assert '<link rel="stylesheet" href="/edge.css">' in html
    assert '/app-icon.svg' in html
    # The real Acme mark, not a text wordmark spelling out the brand.
    assert "/acme-logo-white.svg" in html
    assert ">BIO&#8209;RAD<" not in html


def test_signed_out_state_stops_the_bounce():
    """/.auth/logout redirecting to "/" used to sign the same person straight back
    in. The signed-out copy is what gives that redirect a terminus."""
    html = signin_page.render(signed_out=True)
    assert "You&#8217;ve been signed out" in html or "You've been signed out" in html
    assert "sign back in below" in html
    # Still offers the button — a person who signed out to CHANGE account needs it.
    assert "Sign in with Microsoft" in html


def test_a_deep_link_survives_the_round_trip_whole():
    """The target is a VALUE inside the login endpoint's query string, so its own
    "&" must be encoded or everything after it is parsed as a further parameter of
    the login URL and silently dropped."""
    html = signin_page.render(next_url="/reqs/abc?tab=a&focus=b")
    assert "post_login_redirect_uri=/reqs/abc%3Ftab%3Da%26focus%3Db" in html
    # The un-encoded form would have truncated the target at the ampersand.
    assert "post_login_redirect_uri=/reqs/abc?tab=a&focus=b" not in html


class TestNextIsNeverOffOrigin:
    """`next` rides in on a query string and ends up in a post-sign-in redirect, so
    an absolute URL here is an open redirect wearing Acme branding."""

    def test_an_ordinary_path_is_kept(self):
        assert signin_page.safe_next("/reqs/x?a=1") == "/reqs/x?a=1"

    def test_absolute_urls_are_refused(self):
        assert signin_page.safe_next("https://elsewhere.example/x") == "/"
        assert signin_page.safe_next("http://elsewhere.example") == "/"

    def test_protocol_relative_is_refused(self):
        # Starts with "/" but a browser reads it as absolute.
        assert signin_page.safe_next("//elsewhere.example/x") == "/"

    def test_backslash_form_is_refused(self):
        # Some browsers treat "\" as a path separator, so this escapes too.
        assert signin_page.safe_next("/\\elsewhere.example") == "/"

    def test_scheme_relative_junk_and_blanks_fall_back(self):
        for value in ["javascript:alert(1)", "", None, "   ", "reqs/x"]:
            assert signin_page.safe_next(value) == "/"

    def test_the_rejected_value_never_reaches_the_html(self):
        html = signin_page.render(next_url="https://elsewhere.example/x")
        assert "elsewhere.example" not in html
