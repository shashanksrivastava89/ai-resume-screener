"""Outbound email (2026-09-06): a seam, with a queue behind it.

WHY. Mentioning someone who does not use the app (yet) should reach them anyway
— a comment that says "@Bhavika, what do you make of this résumé?" is how the tool
spreads, and an @-mention that goes nowhere teaches people not to use it. The app
has no mail path today: Graph ``Mail.Send`` needs a sending mailbox and admin
consent (a Cloud Admin ticket, the ISM-274462 class), so delivery cannot be built
end to end from here. What CAN be built is the whole flow up to the wire, with
the wire itself swappable:

  configured  — ``MAIL_SENDER`` (the mailbox UPN to send as) set AND a Graph
                token obtainable through DefaultAzureCredential → the message is
                sent with ``POST /users/{MAIL_SENDER}/sendMail``.
  otherwise   — the message is QUEUED to ``signals/outbox.jsonl`` and the
                ``notify`` event records ``channel: "email", delivered: false``,
                so the audit trail shows the intent and an operator can see what
                would have gone out. Nothing is silently dropped.

Attachments are ``[{name, content_type, bytes}]``; the caller decides what may
travel (the résumé attachment is a feature flag, off by default — a résumé is a
candidate's PII and sending it outside the app's access model is HR/Legal's call,
not a developer's).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from resume_screener.events import emit_event
from resume_screener.locking import file_lock

log = logging.getLogger(__name__)

OUTBOX_LOG = Path("signals/outbox.jsonl")
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sender() -> str:
    return os.environ.get("MAIL_SENDER", "").strip()


def configured() -> bool:
    return bool(sender())


class MailError(Exception):
    """Delivery failed for a stated reason; the message was queued instead."""


def _default_token() -> str:
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential().get_token(_GRAPH_SCOPE).token


def _http_post(url: str, headers: dict, body: bytes) -> int:
    # Only ever the Graph API over https — enforced, not just assumed (bandit B310).
    if not url.startswith("https://graph.microsoft.com/"):
        raise ValueError("mail goes to Microsoft Graph only")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
        return resp.status


def graph_send(message: dict, *, token_provider: Callable[[], str] | None = None,
               post: Callable[[str, dict, bytes], int] | None = None) -> None:
    """Send one message as MAIL_SENDER through Graph. Raises MailError on any
    failure with a reason an operator can act on."""
    who = sender()
    if not who:
        raise MailError("MAIL_SENDER is not set")
    try:
        token = (token_provider or _default_token)()
    except Exception as exc:  # noqa: BLE001
        raise MailError(f"no Graph token: {type(exc).__name__}") from exc
    url = f"https://graph.microsoft.com/v1.0/users/{who}/sendMail"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        status = (post or _http_post)(url, headers, json.dumps(message).encode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise MailError("Graph refused sendMail — the app's identity lacks Mail.Send "
                            "consent for this mailbox") from exc
        raise MailError(f"Graph returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MailError(f"Graph unreachable: {exc}") from exc
    if status not in (200, 202):
        raise MailError(f"Graph returned HTTP {status}")


def _graph_message(to: str, subject: str, body: str, attachments: list[dict]) -> dict:
    msg: dict = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": True,
    }
    if attachments:
        msg["message"]["attachments"] = [{
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": a["name"],
            "contentType": a.get("content_type") or "application/octet-stream",
            "contentBytes": base64.b64encode(a["bytes"]).decode("ascii"),
        } for a in attachments]
    return msg


def send(
    to: str,
    subject: str,
    body: str,
    *,
    actor: str,
    req_id: str | None = None,
    kind: str = "email",
    attachments: list[dict] | None = None,
    data: dict | None = None,
    outbox: str | Path = OUTBOX_LOG,
    transport: Callable[[dict], None] | None = None,
) -> dict:
    """Deliver if configured, else queue. Always records a ``notify`` event with
    ``channel: "email"`` and whether it was delivered, so the audit trail and the
    recipient's bell (if they ever sign in) both know it happened.

    Returns {delivered, queued, reason}."""
    to = str(to or "").strip()
    if not to:
        return {"delivered": False, "queued": False, "reason": "no recipient"}
    attachments = attachments or []
    outcome = {"delivered": False, "queued": False, "reason": ""}
    if configured():
        try:
            (transport or graph_send)(_graph_message(to, subject, body, attachments))
            outcome["delivered"] = True
        except MailError as exc:
            outcome["reason"] = str(exc)
            log.warning("mailer: not delivered to %s — %s", to, exc)
    else:
        outcome["reason"] = "email delivery is not configured (MAIL_SENDER unset)"

    if not outcome["delivered"]:
        # Queue the message — text only; attachment bytes are never written to a
        # log, only their names, so a résumé never lands in signals/.
        path = Path(outbox)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": _now(), "to": to, "subject": subject, "body": body,
                 "attachments": [a["name"] for a in attachments], "actor": actor,
                 "req_id": req_id, "reason": outcome["reason"]}
        with file_lock(path), path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        outcome["queued"] = True

    emit_event("notify", actor=actor, req_id=req_id, summary=subject,
               data={"to": to, "kind": kind, "channel": "email",
                     "delivered": outcome["delivered"], "queued": outcome["queued"],
                     "attachments": [a["name"] for a in attachments],
                     **(data or {})})
    return outcome


def queued(outbox: str | Path = OUTBOX_LOG) -> list[dict]:
    """Everything waiting in the outbox, oldest first (operator view)."""
    path = Path(outbox)
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out
