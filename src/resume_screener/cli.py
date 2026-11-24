"""CLI (`screener`, CLAUDE.md §7): check · rubric · run · explain · ui + hidden debug group.

Structure
---------
- `app` holds the commands recruiters/operators use.
- `debug_app` (hidden from --help) holds one tester per pipeline stage
  (ingest/redact/parse/findings/features/l1/l2/…) — each runs JUST that stage,
  cache-first, so you can inspect any stage's output without a full run.
  They're the fastest way to debug "why did candidate X score this way".

Conventions used by every command
---------------------------------
- Expected failures (ConfigError/PolicyError/RubricError/…) print as one
  friendly "✗ …" line and exit 1 — no tracebacks for operator mistakes.
- Heavy imports live INSIDE command functions: `screener --help` must stay
  snappy and must not require Azure SDKs to even parse.
- Nothing here prints secret values — hints name the secret/variable, never
  its content.
"""

from __future__ import annotations

import os
import sys

import typer

app = typer.Typer(help="Resume Screener — decision-support shortlisting. Never auto-rejects.")
debug_app = typer.Typer(help="Hidden testers for individual pipeline stages.")
app.add_typer(debug_app, name="debug", hidden=True)
db_app = typer.Typer(help="Database schema management (Alembic migrations).")
app.add_typer(db_app, name="db")
data_app = typer.Typer(help="Stored résumé files: checks and one-off sweeps.")
app.add_typer(data_app, name="data")


def _alembic_config():
    """Alembic Config pinned to the repo-root alembic.ini, regardless of cwd."""
    from pathlib import Path

    from alembic.config import Config

    ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    if not ini.exists():
        raise typer.BadParameter(f"alembic.ini not found at {ini}")
    return Config(str(ini))


@app.callback()
def main() -> None:
    """Resume Screener CLI."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # ✓/✗ on Windows consoles
    except Exception:
        pass
    # `.env` -> os.environ FIRST, and from the ORIGINAL cwd: the behavioural switches
    # (APP_MODE, DEV_USER/DEV_ROLE, FEATURE_*) are read straight off os.environ, which
    # pydantic-settings never populates, so before this they did nothing when set in the
    # file that documents them. Real env vars still win, and it must run before
    # bootstrap() chdirs into DATA_ROOT or we would read the share's .env instead.
    from resume_screener.config import export_env_file

    export_env_file()

    # P26: resolve DATA_ROOT (container: the Azure Files mount) before any command
    # touches a relative path. A no-op locally (DATA_ROOT unset).
    from resume_screener.dataroot import bootstrap

    bootstrap()


@app.command(name="batch-prep", hidden=True)
def batch_prep_command(req_id: str = typer.Argument(..., help="Requisition id (JD stem)")) -> None:
    """(Internal) Drain a requisition's staged résumé uploads — split bulk PDFs,
    de-duplicate, index. Spawned detached by the API on upload; safe to run by
    hand after a crash (a second instance exits immediately)."""
    from resume_screener import batch_prep

    raise typer.Exit(code=batch_prep.run_worker(req_id))


def _tiny_test_image() -> bytes:
    """A small in-memory PNG with text — the cheapest possible DI round-trip."""
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 100), "white")
    ImageDraw.Draw(img).text((20, 40), "Resume Screener doctor check", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _package_version(pkg: str) -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(pkg)
    except PackageNotFoundError:
        return "missing"


def _extraction_for(cfg):
    """The extraction settings in force for a command (EXTRACTION_MODE wins over
    config.yaml) — every path that reads a résumé reads it the same way."""
    from resume_screener.config import effective_extraction

    return effective_extraction(cfg)[0]


@app.command()
def check() -> None:
    """5-point doctor: sign-in, AI credentials (Key Vault secrets, or a managed-identity
    token under AI_AUTH=identity), Document Intelligence, chat, embeddings.

    The checks form a dependency chain (no sign-in -> can't read secrets -> can't
    call services), so later checks print "skipped — fix X first" instead of
    drowning the real problem in follow-on errors. Setup doc §H maps each ✗ to
    its manual fix. Exit code 0 only when all five pass — P1's hard gate."""
    from resume_screener.config import (
        AI_TOKEN_SCOPE, ConfigError, _credential, api_keys_for, invalidate_api_keys,
        load_settings,
    )

    failures = 0

    def show(ok: bool, label: str, detail: str = "") -> None:
        nonlocal failures
        if not ok:
            failures += 1
        mark = "✓" if ok else "✗"
        typer.echo(f"{mark} {label}" + (f" — {detail}" if detail else ""))

    def hint_of(exc: Exception) -> str:
        if isinstance(exc, ConfigError):
            return str(exc)
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        return f"{type(exc).__name__}: {first_line[:140]}"

    try:
        settings = load_settings()
    except ConfigError as exc:
        show(False, ".env", str(exc))
        raise typer.Exit(1)

    # 1) Azure sign-in
    try:
        _credential().get_token("https://vault.azure.net/.default")
        show(True, "Azure sign-in", "DefaultAzureCredential has a valid token")
        signed_in = True
    except Exception:
        tenant = f" --tenant {settings.azure_tenant_id}" if settings.azure_tenant_id else ""
        show(False, "Azure sign-in", f"run:  az login{tenant}")
        signed_in = False

    # 2) AI-service credentials (values never shown). AI_AUTH=key (the default) reads the
    #    two API keys from Key Vault — fresh, not from the cache, so this proves the vault
    #    answers NOW; AI_AUTH=identity asks Entra for a Cognitive Services token instead
    #    and never reads an AI key. Checks 3-5 then prove the services accept it.
    from resume_screener.azure_clients import describe_auth

    keys = None
    ai_ready = False
    identity = settings.ai_auth == "identity"
    auth_label = "AI auth (managed identity)" if identity else "Key Vault secrets"
    if not signed_in:
        show(False, auth_label, "skipped — fix Azure sign-in first")
    elif identity:
        try:
            _credential().get_token(AI_TOKEN_SCOPE)
            show(True, auth_label, describe_auth(settings))
            ai_ready = True
        except Exception as exc:
            show(False, auth_label,
                 f"{hint_of(exc)} — AI_AUTH=identity needs a Cognitive Services token")
    else:
        try:
            invalidate_api_keys()
            keys = api_keys_for(settings)
            show(True, auth_label,
                 f"'{settings.azure_openai_api_key_secret}' and "
                 f"'{settings.documentintelligence_api_key_secret}' readable (values not shown) "
                 f"· AI_AUTH=key")
            ai_ready = True
        except Exception as exc:
            show(False, auth_label, hint_of(exc))
    role_hint = (" — in AI_AUTH=identity the identity needs 'Cognitive Services User' on this "
                 "resource" if identity else " — check DOCUMENTINTELLIGENCE_ENDPOINT and the key secret")

    # 3) Document Intelligence (tiny in-memory image through prebuilt-layout). Since
    #    2026-10-14 it reads only résumés with no text of their own (hybrid, the
    #    default); the check still proves the service answers for those.
    try:
        from resume_screener.config import effective_extraction, load_app_config

        _ext_mode = effective_extraction(load_app_config())[0].mode
    except Exception:  # noqa: BLE001 — a missing config.yaml is reported below
        _ext_mode = "hybrid"
    di_role = ("reads only résumés with no text of their own" if _ext_mode == "hybrid"
               else "reads every résumé (EXTRACTION_MODE=di-only)")
    if ai_ready:
        try:
            from resume_screener.azure_clients import get_document_intelligence_client

            di = get_document_intelligence_client(settings, keys)
            poller = di.begin_analyze_document(
                "prebuilt-layout", _tiny_test_image(), content_type="application/octet-stream"
            )
            result = poller.result()
            pages = len(result.pages or [])
            show(True, "Document Intelligence",
                 f"prebuilt-layout analyzed a test image ({pages} page) · {di_role}")
        except Exception as exc:
            show(False, "Document Intelligence", f"{hint_of(exc)}{role_hint}")
    else:
        show(False, "Document Intelligence", "skipped — need the AI credentials (check 2)")

    # 4) Chat model (also decides v1 vs fallback API path, once)
    client_info = None
    if ai_ready:
        try:
            from resume_screener.azure_clients import get_openai_client

            client_info = get_openai_client(settings, keys)
            show(True, f"Chat model '{settings.azure_openai_chat_deployment}'",
                 f"replied via {client_info.path}")
        except ConfigError as exc:
            show(False, f"Chat model '{settings.azure_openai_chat_deployment}'", str(exc))
        except Exception as exc:
            show(False, f"Chat model '{settings.azure_openai_chat_deployment}'",
                 f"{hint_of(exc)} — check AZURE_OPENAI_ENDPOINT / deployment name")
    else:
        show(False, "Chat model", "skipped — need the AI credentials (check 2)")

    # 5) Embeddings
    if client_info:
        try:
            emb = client_info.client.embeddings.create(
                model=settings.azure_openai_embedding_deployment, input="ping"
            )
            dims = len(emb.data[0].embedding)
            show(True, f"Embeddings '{settings.azure_openai_embedding_deployment}'",
                 f"{dims} dimensions")
        except Exception as exc:
            show(False, f"Embeddings '{settings.azure_openai_embedding_deployment}'",
                 f"{hint_of(exc)} — check AZURE_OPENAI_EMBEDDING_DEPLOYMENT in .env")
    else:
        show(False, "Embeddings", "skipped — chat model check must pass first")

    # Database (informational — NOT part of the 5-point Azure gate, so a missing
    # local Postgres never blocks Azure-only workflows).
    try:
        from sqlalchemy import inspect, text

        from resume_screener.db.engine import get_engine

        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        # Cover the v2-domain tables (P49): flag if the schema predates them so the
        # operator runs the migration before flipping a *_BACKEND to db.
        present = set(inspect(engine).get_table_names())
        v2 = {"hr_preference", "app_event", "hm_take", "checklist_approval", "legal_hold"}
        missing = sorted(v2 - present)
        if missing:
            typer.echo(f"• Database — connected, but {len(missing)} v2 table(s) missing "
                       f"({', '.join(missing)}); run `screener db upgrade`")
        else:
            typer.echo("✓ Database — connection OK (schema current)")
    except Exception as exc:
        typer.echo(
            f"• Database — not reachable ({hint_of(exc)}); "
            "run `docker compose up -d db` then `screener db upgrade` (optional until hosting)"
        )

    # Entry mode (APP_MODE) — which of the three ways in this process is serving, and
    # whether it is actually configured for that. Counted in the gate ONLY for real
    # misconfigurations (`ok is False`): in dev nothing here can fail, so the historic
    # "5 checks" contract is unchanged, while `APP_MODE=dev` on a hosted app — the
    # anonymous-SuperAdmin incident — now fails the doctor instead of shipping.
    # `ok is None` items are gates nobody can verify from inside the process (admin
    # consent, Easy Auth enforcement); they print as caveats and never fail the run.
    from resume_screener import modes

    typer.echo()
    typer.echo(f"Entry mode — APP_MODE={modes.resolve()}")
    for chk in modes.preflight():
        if chk.ok is None:
            typer.echo(f"  • {chk.name} — {chk.detail}")
        else:
            show(chk.ok, f"  {chk.name}", chk.detail)

    # Extraction (2026-10-14) — where résumé text is read, and an offline self-test of
    # the routing. Counted in the gate only for a real misconfiguration (an invalid
    # EXTRACTION_MODE, a failed self-test), like the entry-mode section above.
    from resume_screener import localextract
    from resume_screener.config import load_app_config

    typer.echo()
    try:
        ext_checks = localextract.preflight(load_app_config())
    except ConfigError as exc:
        ext_checks = []
        show(False, "Extraction", str(exc))
    if ext_checks:
        typer.echo("Extraction")
    for chk in ext_checks:
        if chk.ok is None:
            typer.echo(f"  • {chk.name} — {chk.detail}")
        else:
            show(chk.ok, f"  {chk.name}", chk.detail)

    # Decision verdicts at rest (fieldcrypto, 2026-10-14) — informational unless a key
    # is configured but cannot be read, which would stop every decision being saved.
    from resume_screener import fieldcrypto

    state, detail = fieldcrypto.status()
    if state == "unavailable":
        show(False, "Decision verdicts", detail)
    else:
        typer.echo(f"{'✓' if state == 'encrypted' else '•'} Decision verdicts — {state}: {detail}")

    # The hiring-manager portal as THIS deployment resolves it (2026-10-14). HR asked
    # to share shortlists with hiring managers and was told the feature "isn't in the
    # tool": under FLAGS_BACKEND=db the flag reads its DB row, whose default can be off
    # while features.yaml says on. Say which, so nobody has to guess.
    try:
        from resume_screener import feature_flags

        portal = "on" if feature_flags.flag_enabled("hm_portal") else (
            "OFF — hiring managers cannot open a shared shortlist; a Super Admin turns "
            "it on under Governance → Feature flags")
        typer.echo(f"Hiring-manager portal — {portal}")
    except Exception as exc:  # noqa: BLE001 — informational, never fails the doctor
        typer.echo(f"Hiring-manager portal — could not be read ({hint_of(exc)})")

    # Where this deployment stores and processes candidate data, in its own plain
    # words. Not a check — nothing can verify it — but a deployment serving the EU
    # or California from India should be able to SAY so, and an operator should be
    # able to see at a glance whether it has been told (2026-09-12).
    processing = (os.environ.get("PROCESSING_LOCATIONS") or "").strip()
    typer.echo()
    if processing:
        typer.echo(f"Processing locations — {processing}")
    else:
        typer.echo("Processing locations — not set (PROCESSING_LOCATIONS); "
                   "the hiring-rules popover shows nothing about where data goes")

    typer.echo()
    if failures:
        typer.echo(f"{failures} check(s) failed (5 Azure + entry mode).")
        raise typer.Exit(1)
    typer.echo("All 5 Azure checks passed and the entry mode is configured — ready to run.")


@db_app.command("upgrade")
def db_upgrade(revision: str = typer.Argument("head", help="Target revision.")) -> None:
    """Apply migrations up to REVISION (default: head). Safe to run repeatedly."""
    from alembic import command

    command.upgrade(_alembic_config(), revision)
    typer.echo(f"✓ Database migrated to {revision}.")


@db_app.command("downgrade")
def db_downgrade(revision: str = typer.Argument(..., help="Target revision (e.g. -1).")) -> None:
    """Roll migrations back to REVISION."""
    from alembic import command

    command.downgrade(_alembic_config(), revision)
    typer.echo(f"✓ Database downgraded to {revision}.")


@db_app.command("current")
def db_current() -> None:
    """Show the revision the database is currently at."""
    from alembic import command

    command.current(_alembic_config(), verbose=True)


@db_app.command("import")
def db_import() -> None:
    """Backfill the flat-file logs into the database (one-time, safe to re-run).

    Carries the existing history across when a domain is switched to the DB
    backend: decisions.jsonl → decision, audit_log.jsonl → audit_entry,
    review_status.json → review_signoff. Each domain is skipped if its table
    already has rows, so re-running never duplicates append-only history."""
    from resume_screener.db.importers import import_all
    from resume_screener.db.session import session_scope

    try:
        with session_scope() as session:
            reports = import_all(session)
    except Exception as exc:  # a missing/unreachable DB shouldn't dump a traceback
        typer.echo(f"✗ Import failed: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}")
        typer.echo("  Is the database up and migrated?  docker compose up -d db && screener db upgrade")
        raise typer.Exit(1)
    for r in reports:
        if r["skipped"]:
            typer.echo(f"• {r['domain']}: skipped ({r['reason']})")
        else:
            extra = f" ({r['reason']})" if r.get("reason") else ""
            typer.echo(f"✓ {r['domain']}: imported {r['added']} record(s){extra}")
    typer.echo("\nDone. Set FEEDBACK_BACKEND=db / GOVERNANCE_BACKEND=db / RUN_INDEX_BACKEND=db "
               "(and the v2 switches PREFS_BACKEND=db / EVENTS_BACKEND=db / HM_TAKES_BACKEND=db / "
               "FLAGS_BACKEND=db) to read from the database.")


@db_app.command("encrypt-decisions")
def db_encrypt_decisions(
    rewrap: bool = typer.Option(
        False, "--rewrap",
        help="Also re-seal rows sealed under an OLDER key version (after rotating the "
             "secret). Only then may the old version be disabled."),
) -> None:
    """Seal every plain decision row with the decision key (one-off; safe to re-run).

    Run once after DECISION_KEY_SECRET is first configured: rows written since are
    sealed on write, rows from before stay plain until this sweeps them. Each row's
    AI suggestion and note move into `verdict_enc`; the plain columns are emptied.
    Prints counts only — never a value."""
    from resume_screener import events, fieldcrypto, rbac
    from resume_screener.db.repositories import decisions as decisions_repo
    from resume_screener.db.session import session_scope

    if not fieldcrypto.enabled():
        typer.echo(f"✗ {fieldcrypto.ENV_SECRET} is not set — there is no key to seal with. "
                   "Create the secret in Key Vault and set the variable first.")
        raise typer.Exit(1)
    try:
        with session_scope() as session:
            sealed, resealed, untouched = decisions_repo.encrypt_plain_rows(
                session, rewrap=rewrap)
    except fieldcrypto.DecisionKeyUnavailable as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)
    except Exception as exc:  # a missing/unreachable DB shouldn't dump a traceback
        typer.echo(f"✗ Could not seal decisions: {type(exc).__name__}: "
                   f"{str(exc).splitlines()[0][:160]}")
        typer.echo("  Is the database up and migrated?  screener db upgrade")
        raise typer.Exit(1)
    user = rbac.get_current_user(None) or "cli"
    if sealed or resealed:
        events.emit_event("decisions_encrypted", actor=user, actor_role="cli",
                          summary=(f"{sealed} decision verdict(s) encrypted at rest"
                                   + (f", {resealed} re-sealed under the current key"
                                      if resealed else "")),
                          data={"sealed": sealed, "resealed": resealed,
                                "untouched": untouched})
    if rewrap:
        typer.echo(f"✓ Encrypted {sealed} decision row(s) · re-sealed {resealed} under the "
                   f"current key · {untouched} already current.")
    else:
        typer.echo(f"✓ Encrypted {sealed} decision row(s) · {untouched} were already encrypted.")


@data_app.command("sanitize-pdfs")
def data_sanitize_pdfs(
    fix: bool = typer.Option(
        False, "--fix", help="Clean the files that carry active content (default: report only)."),
) -> None:
    """Find stored PDFs that carry active content — JavaScript, launch actions,
    embedded files — and, with --fix, clean them (pen-test RS-04).

    Intake cleans every PDF from 2026-10-16; this is for files stored before then
    (the download routes already serve them cleaned). Report-only by default,
    because cleaning changes a file's bytes and so its candidate id: decisions on a
    file that was already screened stay in the log but will not carry forward
    after a re-screen, and each such file is named. Prints file names and what was
    found — never résumé text."""
    from pathlib import Path

    from resume_screener import events, pdfsafe, rbac
    from resume_screener.api import service
    from resume_screener.config import load_app_config

    cfg = load_app_config()
    roles = Path(cfg.ats.roles_dir)
    paths = (sorted(roles.glob("*/resumes/*.pdf")) + sorted(roles.glob("*/resumes/_sources/*.pdf"))
             + sorted(Path(cfg.ats.resumes_dir).glob("*.pdf")))
    user = rbac.get_current_user(None) or "cli"
    found = cleaned = screened = 0
    for path in paths:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        clean, removed = pdfsafe.strip(data)
        if not removed:
            continue
        found += 1
        try:
            req_id = path.relative_to(roles).parts[0]
        except ValueError:
            req_id = None                      # the legacy shared folder
        mapped = req_id and any(Path(v).resolve() == path.resolve()
                                for v in service._private_map(req_id).values())
        screened += 1 if mapped else 0
        note = (" — already screened: its decisions stay in the log but won't carry "
                "forward after a re-screen" if mapped else "")
        typer.echo(f"{'✓ cleaned' if fix else '!'} {path.as_posix()} · {', '.join(removed)}{note}")
        if fix:
            tmp = path.with_name(f".{path.name}.clean.tmp")
            tmp.write_bytes(clean)
            os.replace(tmp, path)
            cleaned += 1
            events.emit_event("pdf_active_content_removed", actor=user, actor_role="cli",
                              req_id=req_id, summary="Removed active content from 1 résumé file",
                              data={"file": path.name, "removed": removed})
    if not found:
        typer.echo(f"✓ {len(paths)} stored PDF(s) checked — none carry active content.")
    elif fix:
        typer.echo(f"✓ Cleaned {cleaned} of {len(paths)} stored PDF(s) ({screened} already screened).")
    else:
        typer.echo(f"{found} of {len(paths)} stored PDF(s) carry active content "
                   f"({screened} already screened). Run with --fix to clean them.")


@db_app.command("seed")
def db_seed() -> None:
    """Seed the region base framework (Phase 7): regions, jurisdiction→region mapping,
    the feature catalog + per-region defaults, and the dev user's all-regions grant.
    Idempotent — safe to re-run. Reference data only; enforcement/SSO ride on P27."""
    from resume_screener import rbac
    from resume_screener.db.seed import seed_region_framework
    from resume_screener.db.session import session_scope

    user, role = rbac.get_identity()
    try:
        with session_scope() as session:
            report = seed_region_framework(session, dev_user=user, dev_role=role)
    except Exception as exc:  # a missing/unreachable DB shouldn't dump a traceback
        typer.echo(f"✗ Seed failed: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}")
        typer.echo("  Is the database up and migrated?  docker compose up -d db && screener db upgrade")
        raise typer.Exit(1)
    typer.echo(f"✓ Seeded {report['regions']} regions, {report['jurisdictions']} "
               f"jurisdictions, {report['features']} features.")
    if report["dev_grant"]:
        typer.echo(f"✓ Granted '{report['dev_grant']}' an all-regions grant (local dev sees every region).")


@db_app.command("revision")
def db_revision(
    message: str = typer.Option(..., "-m", "--message", help="Revision description."),
    autogenerate: bool = typer.Option(
        True, "--autogenerate/--empty", help="Diff models vs DB to draft the migration."
    ),
) -> None:
    """Create a new migration script (autogenerate by default)."""
    from alembic import command

    command.revision(_alembic_config(), message=message, autogenerate=autogenerate)


@debug_app.command("ingest")
def debug_ingest(limit: int = typer.Option(3, "--limit", help="How many resumes to extract.")) -> None:
    """Extract the first N resumes via the ATS port + Document Intelligence (cached)."""
    import time
    from pathlib import Path

    from resume_screener.ats import get_ats
    from resume_screener.azure_clients import get_document_intelligence_client
    from resume_screener.cache import JsonCache
    from resume_screener.config import ConfigError, api_keys_for, load_app_config, load_settings
    from resume_screener.ingest import extract_file
    from resume_screener.models import Requisition

    try:
        settings = load_settings()
        cfg = load_app_config()
        keys = api_keys_for(settings)
    except ConfigError as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)

    ats = get_ats(cfg)
    reqs = ats.fetch_requisitions()
    typer.echo(
        f"ATS provider '{cfg.ats.provider}': {len(reqs)} requisition(s) in {cfg.ats.jd_dir}/, "
        f"default jurisdiction {cfg.policy.default_jurisdiction}"
    )
    req = reqs[0] if reqs else Requisition(
        req_id="debug", role_title="(no JDs yet — added in P4)", jd_ref="",
        job_location=cfg.policy.default_jurisdiction,
    )
    candidates = ats.fetch_candidates(req)[:limit]
    if not candidates:
        typer.echo(f"No resumes found in {cfg.ats.resumes_dir}/ — add 3–5 PDF/DOCX files (setup §G).")
        raise typer.Exit(1)

    di = get_document_intelligence_client(settings, keys)
    cache = JsonCache(cfg.runtime.cache_dir)
    for cand in candidates:
        for doc in ats.fetch_documents(cand):
            start = time.perf_counter()
            res = extract_file(di, cfg.models.document_model, Path(doc.uri), cache,
                               _extraction_for(cfg))
            elapsed = time.perf_counter() - start
            source = "cache" if res.from_cache else res.extractor
            conf = f"{res.extraction_confidence:.3f}" if res.extraction_confidence is not None else "  n/a"
            typer.echo(
                f"{source:>21} | {elapsed:6.2f}s | id {res.candidate_id} | {res.page_count}p "
                f"| conf {conf} | {len(res.text):>6} chars | {doc.file_name}"
            )


@app.command()
def rubric(
    jd: str = typer.Option(..., "--jd", help="Path to the JD (markdown/text)."),
) -> None:
    """Draft a screening rubric from a JD and save it as a versioned, editable YAML."""
    from pathlib import Path

    from resume_screener.azure_clients import get_openai_client
    from resume_screener.config import ConfigError, api_keys_for, load_settings
    from resume_screener.rubric import RubricError, draft_rubric, save_rubric

    jd_path = Path(jd)
    if not jd_path.exists():
        typer.echo(f"✗ JD not found: {jd_path}")
        raise typer.Exit(1)

    try:
        settings = load_settings()
        keys = api_keys_for(settings)
        info = get_openai_client(settings, keys)
        jd_text = jd_path.read_text(encoding="utf-8")
        result = draft_rubric(
            jd_text, str(jd_path), info.client, settings.azure_openai_chat_deployment
        )
        path = save_rubric(result, jd_path)
    except (ConfigError, RubricError) as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)

    typer.echo(f"Role: {result.role_title}   (rubric version {result.version})")
    gates = [r for r in result.requirements if r.kind == "hard_gate"]
    if gates:
        typer.echo("\nHard gates (objective pass/fail from the JD — no weight):")
        for g in gates:
            typer.echo(f"  [{g.id}] {g.text}")
    else:
        typer.echo("\nHard gates: none stated in the JD.")
    for kind, title in (("must_have", "Must-haves"), ("nice_to_have", "Nice-to-haves")):
        rows = [r for r in result.requirements if r.kind == kind]
        typer.echo(f"\n{title} ({len(rows)}):")
        for r in rows:
            typer.echo(f"  [{r.id}] {r.weight:5.1%}  {r.type:<13} {r.text}")
    typer.echo(f"\nSaved: {path}")
    typer.echo("Edit the YAML freely (texts, kinds, weights) — weights renormalize on load.")


@debug_app.command("policy")
def debug_policy(
    jurisdiction: str = typer.Option(
        None, "--jurisdiction", help="Job-location code (IN, EU, US-NYC, …). Default from config.yaml."
    ),
) -> None:
    """Show the effective policy + enabled signal set for a jurisdiction (offline)."""
    from resume_screener.config import ConfigError, load_app_config
    from resume_screener.policy import PolicyError, resolve_policy
    from resume_screener.registry import RegistryError, apply_policy, load_registry

    try:
        cfg = load_app_config()
        code = (jurisdiction or cfg.policy.default_jurisdiction).strip()
        policy = resolve_policy(code, cfg.policy.policies_dir)
        registry = load_registry(cfg.signals_file)
        enabled, excluded = apply_policy(registry, policy)
    except (ConfigError, PolicyError, RegistryError) as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)

    typer.echo(f"Jurisdiction: {policy.jurisdiction}   (policy pack: {policy.pack} + base floor)")
    typer.echo("Floor (never varies): " + ", ".join(k for k, v in policy.floor.items() if v))
    typer.echo(f"\nEnabled signals ({len(enabled)} of {len(registry.signals)}):")
    for s in enabled:
        params = f"   params: {s.params}" if s.params else ""
        typer.echo(f"  {s.id:<32} {s.kind:<9} {s.direction:<6} cap ±{s.cap_points:g}{params}")
    disabled = sorted({x.id for x in registry.signals} - {s.id for s in enabled})
    if disabled:
        typer.echo("Disabled here: " + ", ".join(disabled))
    typer.echo(f"\nNever scored (excluded, {len(excluded)}): " + ", ".join(e.id for e in excluded))
    typer.echo("\nObligations checklist (displayed, not enforced):")
    for o in policy.obligations:
        typer.echo(f"  • {o}")
    typer.echo(f"\nMonitoring categories: {', '.join(policy.monitoring_categories) or '(none)'}")
    if policy.keep_at_least_days:
        typer.echo(f"Retention note: keep audit records at least {policy.keep_at_least_days} days.")
    if policy.purge_by_days:
        typer.echo(f"Retention note: purge candidate PII {policy.purge_by_days} days after close.")
    for note in policy.notes:
        typer.echo(f"Note: {note}")


@debug_app.command("migrate-sidecars")
def debug_migrate_sidecars() -> None:
    """One-time throwaway (P29): fold legacy jds/<stem>.location.json sidecars into
    the consolidated jds/<stem>.req.json, then delete the legacy files. The data is
    disposable test data — this exists so local roles don't need recreating."""
    import json
    from pathlib import Path

    from resume_screener.ats.local_folder import save_req_meta
    from resume_screener.config import ConfigError, load_app_config

    try:
        cfg = load_app_config()
    except ConfigError as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)
    jd_dir = Path(cfg.ats.jd_dir)
    legacy_suffix = ".location.json"
    migrated = 0
    for legacy in sorted(jd_dir.glob(f"*{legacy_suffix}")):
        stem = legacy.name[: -len(legacy_suffix)]
        try:
            location = (json.loads(legacy.read_text(encoding="utf-8")) or {}).get("job_location")
        except (OSError, ValueError):
            location = None
        if location:
            # req_meta_path keys on the stem, so the .md handle works for .txt JDs too
            save_req_meta(jd_dir / f"{stem}.md", job_location=location)
            typer.echo(f"✓ {legacy.name} -> {stem}.req.json (job_location={location})")
        else:
            typer.echo(f"✓ {legacy.name} removed (no job_location inside)")
        legacy.unlink()
        migrated += 1
    typer.echo(f"{migrated} legacy sidecar(s) migrated.")


@debug_app.command("redact")
def debug_redact(limit: int = typer.Option(1, "--limit", help="How many resumes to redact.")) -> None:
    """Extract then redact N resumes; print BEFORE/AFTER so redaction can be reviewed."""
    from pathlib import Path

    from resume_screener.ats import get_ats
    from resume_screener.azure_clients import get_document_intelligence_client
    from resume_screener.cache import JsonCache
    from resume_screener.config import ConfigError, api_keys_for, load_app_config, load_settings
    from resume_screener.ingest import extract_file
    from resume_screener.models import Requisition
    from resume_screener.redact import record_candidate, redact_text

    try:
        settings = load_settings()
        cfg = load_app_config()
        keys = api_keys_for(settings)
    except ConfigError as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)

    ats = get_ats(cfg)
    req = Requisition(req_id="debug", role_title="debug", jd_ref="",
                      job_location=cfg.policy.default_jurisdiction)
    candidates = ats.fetch_candidates(req)[:limit]
    if not candidates:
        typer.echo(f"No resumes found in {cfg.ats.resumes_dir}/.")
        raise typer.Exit(1)

    di = get_document_intelligence_client(settings, keys)
    cache = JsonCache(cfg.runtime.cache_dir)
    for cand in candidates:
        for doc in ats.fetch_documents(cand):
            extraction = extract_file(di, cfg.models.document_model, Path(doc.uri), cache,
                                      _extraction_for(cfg))
            redaction = redact_text(extraction.text, extraction.candidate_id, doc.file_name)
            map_path = record_candidate(extraction.candidate_id, doc.uri)
            typer.echo(f"\n=== BEFORE (raw extraction) — {doc.file_name} ===\n")
            typer.echo(extraction.text)
            typer.echo(f"\n=== AFTER (redacted) — candidate {redaction.candidate_id} ===\n")
            typer.echo(redaction.redacted_text)
            masked = ", ".join(f"{k}: {v}" for k, v in sorted(redaction.counts.items())) or "nothing"
            typer.echo(f"\nMasked -> {masked}")
            typer.echo(f"Private map updated: {map_path}")


@debug_app.command("parse")
def debug_parse(limit: int = typer.Option(3, "--limit", help="How many resumes to parse.")) -> None:
    """Extract -> redact -> parse N resumes into frozen ResumeProfile artifacts."""
    import json
    import time
    from pathlib import Path

    from resume_screener.ats import get_ats
    from resume_screener.azure_clients import get_document_intelligence_client, get_openai_client
    from resume_screener.cache import JsonCache
    from resume_screener.config import ConfigError, api_keys_for, load_app_config, load_settings
    from resume_screener.ingest import default_artifact_dir, extract_file, freeze_profile, parse_profile
    from resume_screener.models import Requisition
    from resume_screener.redact import record_candidate, redact_text

    try:
        settings = load_settings()
        cfg = load_app_config()
        keys = api_keys_for(settings)
        client_info = get_openai_client(settings, keys)
    except ConfigError as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)

    ats = get_ats(cfg)
    reqs = ats.fetch_requisitions()
    req = reqs[0] if reqs else Requisition(
        req_id="debug", role_title="debug", jd_ref="",
        job_location=cfg.policy.default_jurisdiction,
    )
    candidates = ats.fetch_candidates(req)[:limit]
    if not candidates:
        typer.echo(f"No resumes found in {cfg.ats.resumes_dir}/.")
        raise typer.Exit(1)

    di = get_document_intelligence_client(settings, keys)
    cache = JsonCache(cfg.runtime.cache_dir)
    typer.echo(f"Requisition: {req.req_id} — via {client_info.path}")
    for cand in candidates:
        for doc in ats.fetch_documents(cand):
            start = time.perf_counter()
            extraction = extract_file(di, cfg.models.document_model, Path(doc.uri), cache,
                                      _extraction_for(cfg))
            redaction = redact_text(extraction.text, extraction.candidate_id, doc.file_name)
            profile, cached = parse_profile(
                redaction.redacted_text, extraction.candidate_id, doc.uri,
                client_info.client, settings.azure_openai_chat_deployment, cache,
            )
            artifact = freeze_profile(profile, default_artifact_dir(req.req_id))
            record_candidate(extraction.candidate_id, doc.uri)
            elapsed = time.perf_counter() - start

            display = profile.model_dump()
            display["redacted_text"] = f"<{len(profile.redacted_text)} chars — full text in the artifact>"
            typer.echo(f"\n=== {doc.file_name} -> candidate {profile.candidate_id} ===")
            typer.echo(json.dumps(display, indent=2, ensure_ascii=False))
            typer.echo(
                f"[{'cache' if cached else 'model'} | {elapsed:5.2f}s] frozen: {artifact}"
            )


@debug_app.command("findings")
def debug_findings(limit: int = typer.Option(3, "--limit", help="How many candidates.")) -> None:
    """Rubric-vs-resume findings for N candidates (first shown fully, rest summarized)."""
    import time
    from pathlib import Path

    from resume_screener.ats import get_ats
    from resume_screener.azure_clients import get_document_intelligence_client, get_openai_client
    from resume_screener.cache import JsonCache
    from resume_screener.config import ConfigError, api_keys_for, load_app_config, load_settings
    from resume_screener.findings import compute_findings, freeze_findings
    from resume_screener.ingest import default_artifact_dir, extract_file
    from resume_screener.redact import redact_text
    from resume_screener.rubric import RubricError, latest_rubric_path, load_rubric

    try:
        settings = load_settings()
        cfg = load_app_config()
        keys = api_keys_for(settings)
        client_info = get_openai_client(settings, keys)
    except ConfigError as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)

    ats = get_ats(cfg)
    reqs = ats.fetch_requisitions()
    if not reqs:
        typer.echo(f"No JDs found in {cfg.ats.jd_dir}/ — add one and run 'screener rubric' first.")
        raise typer.Exit(1)
    req = reqs[0]
    rubric_path = latest_rubric_path(req.jd_ref)
    if rubric_path is None:
        typer.echo(f"No rubric for {req.jd_ref} — run: screener rubric --jd {req.jd_ref}")
        raise typer.Exit(1)
    try:
        rubric = load_rubric(rubric_path)
    except RubricError as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)

    candidates = ats.fetch_candidates(req)[:limit]
    if not candidates:
        typer.echo(f"No resumes found in {cfg.ats.resumes_dir}/.")
        raise typer.Exit(1)

    di = get_document_intelligence_client(settings, keys)
    cache = JsonCache(cfg.runtime.cache_dir)
    marks = {"full": "✓", "partial": "~", "none": "✗"}
    n_gates = sum(1 for r in rubric.requirements if r.kind == "hard_gate")
    typer.echo(f"Rubric: {rubric_path.name} (v{rubric.version}, {len(rubric.requirements)} requirements, "
               f"{n_gates} hard gates)")

    for index, cand in enumerate(candidates):
        for doc in ats.fetch_documents(cand):
            start = time.perf_counter()
            extraction = extract_file(di, cfg.models.document_model, Path(doc.uri), cache,
                                      _extraction_for(cfg))
            redaction = redact_text(extraction.text, extraction.candidate_id, doc.file_name)
            record, cached = compute_findings(
                redaction.redacted_text, extraction.candidate_id, rubric,
                client_info.client, settings.azure_openai_chat_deployment, cache,
            )
            artifact = freeze_findings(record, default_artifact_dir(req.req_id))
            elapsed = time.perf_counter() - start

            counts = {m: sum(1 for f in record.findings if f.match == m) for m in marks}
            downgraded = sum(1 for f in record.findings if "Downgraded" in (f.note or ""))
            summary = (
                f"candidate {record.candidate_id}: {counts['full']} full, {counts['partial']} partial, "
                f"{counts['none']} none"
                + (f" ({downgraded} downgraded by evidence check)" if downgraded else "")
                + f" [{'cache' if cached else 'model'} | {elapsed:4.1f}s]"
            )
            if index == 0:
                typer.echo(f"\n=== {summary} ===")
                for f in record.findings:
                    line = f"  {marks[f.match]} [{f.requirement_id}] {f.requirement_text}"
                    typer.echo(line)
                    if f.evidence:
                        typer.echo(f'        evidence: "{f.evidence}"')
                    if f.note:
                        typer.echo(f"        note: {f.note}")
                typer.echo(f"  frozen: {artifact}")
            else:
                typer.echo(summary)


@debug_app.command("features")
def debug_features(
    limit: int = typer.Option(3, "--limit", help="How many candidates."),
    jurisdiction: str = typer.Option(None, "--jurisdiction", help="Policy code (default from config.yaml)."),
) -> None:
    """Computed features per candidate (pure code + one embedding call), policy-parameterized."""
    from datetime import date
    from pathlib import Path

    from resume_screener.ats import get_ats
    from resume_screener.azure_clients import get_document_intelligence_client, get_openai_client
    from resume_screener.cache import JsonCache
    from resume_screener.config import ConfigError, api_keys_for, load_app_config, load_settings
    from resume_screener.embed import clamped_cosine01, embed_texts
    from resume_screener.features import compute_features
    from resume_screener.ingest import extract_file, parse_profile
    from resume_screener.policy import PolicyError, resolve_policy
    from resume_screener.redact import redact_text
    from resume_screener.rubric import RubricError, latest_rubric_path, load_rubric

    try:
        settings = load_settings()
        cfg = load_app_config()
        keys = api_keys_for(settings)
        client_info = get_openai_client(settings, keys)
        policy = resolve_policy(jurisdiction or cfg.policy.default_jurisdiction, cfg.policy.policies_dir)
    except (ConfigError, PolicyError) as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)

    ats = get_ats(cfg)
    reqs = ats.fetch_requisitions()
    if not reqs:
        typer.echo(f"No JDs found in {cfg.ats.jd_dir}/.")
        raise typer.Exit(1)
    req = reqs[0]
    rubric_path = latest_rubric_path(req.jd_ref)
    if rubric_path is None:
        typer.echo(f"No rubric for {req.jd_ref} — run: screener rubric --jd {req.jd_ref}")
        raise typer.Exit(1)
    try:
        rubric = load_rubric(rubric_path)
    except RubricError as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)

    candidates = ats.fetch_candidates(req)[:limit]
    if not candidates:
        typer.echo(f"No resumes found in {cfg.ats.resumes_dir}/.")
        raise typer.Exit(1)

    di = get_document_intelligence_client(settings, keys)
    cache = JsonCache(cfg.runtime.cache_dir)
    jd_text = Path(req.jd_ref).read_text(encoding="utf-8")
    today = date.today()  # debug only; the run pipeline (P11) stamps as_of in run_meta
    typer.echo(f"Jurisdiction {policy.jurisdiction} (pack {policy.pack}) — as_of {today.isoformat()}")

    for cand in candidates:
        for doc in ats.fetch_documents(cand):
            extraction = extract_file(di, cfg.models.document_model, Path(doc.uri), cache,
                                      _extraction_for(cfg))
            redaction = redact_text(extraction.text, extraction.candidate_id, doc.file_name)
            profile, _ = parse_profile(
                redaction.redacted_text, extraction.candidate_id, doc.uri,
                client_info.client, settings.azure_openai_chat_deployment, cache,
            )
            # One batched call covers both texts; the JD is a cache hit after
            # the first candidate, the resume after the first run.
            jd_vec, resume_vec = embed_texts(
                [jd_text, redaction.redacted_text],
                client_info.client, settings.azure_openai_embedding_deployment, cache,
            )
            features = compute_features(
                profile, rubric, policy, as_of=today,
                domain_fit_cosine=round(clamped_cosine01(jd_vec, resume_vec), 4),
            )
            typer.echo(f"\n=== candidate {profile.candidate_id} ===")
            typer.echo(features.model_dump_json(indent=2))


@debug_app.command("l1")
def debug_l1(limit: int = typer.Option(5, "--limit", help="How many candidates.")) -> None:
    """Layer-1 scores for N candidates — findings come from cache/artifacts where
    possible; the scoring itself is pure code (no model calls)."""
    from pathlib import Path

    from resume_screener.ats import get_ats
    from resume_screener.azure_clients import get_document_intelligence_client, get_openai_client
    from resume_screener.cache import JsonCache
    from resume_screener.config import ConfigError, api_keys_for, load_app_config, load_settings
    from resume_screener.findings import compute_findings, freeze_findings
    from resume_screener.ingest import default_artifact_dir, extract_file
    from resume_screener import location_context
    from resume_screener.layer1 import passes_gate, score_layer1
    from resume_screener.redact import redact_text
    from resume_screener.rubric import RubricError, latest_rubric_path, load_rubric
    from resume_screener.scoring_model import ScoringModelError, load_scoring_model

    try:
        settings = load_settings()
        cfg = load_app_config()
        sm = load_scoring_model()
        keys = api_keys_for(settings)
        client_info = get_openai_client(settings, keys)
    except (ConfigError, ScoringModelError) as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)

    ats = get_ats(cfg)
    reqs = ats.fetch_requisitions()
    if not reqs:
        typer.echo(f"No JDs found in {cfg.ats.jd_dir}/.")
        raise typer.Exit(1)
    req = reqs[0]
    rubric_path = latest_rubric_path(req.jd_ref)
    if rubric_path is None:
        typer.echo(f"No rubric for {req.jd_ref} — run: screener rubric --jd {req.jd_ref}")
        raise typer.Exit(1)
    try:
        rubric = load_rubric(rubric_path)
    except RubricError as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)

    candidates = ats.fetch_candidates(req)[:limit]
    if not candidates:
        typer.echo(f"No resumes found in {cfg.ats.resumes_dir}/.")
        raise typer.Exit(1)

    di = get_document_intelligence_client(settings, keys)
    cache = JsonCache(cfg.runtime.cache_dir)
    results = []
    for cand in candidates:
        for doc in ats.fetch_documents(cand):
            extraction = extract_file(di, cfg.models.document_model, Path(doc.uri), cache,
                                      _extraction_for(cfg))
            redaction = redact_text(extraction.text, extraction.candidate_id, doc.file_name)
            record, _ = compute_findings(
                redaction.redacted_text, extraction.candidate_id, rubric,
                client_info.client, settings.azure_openai_chat_deployment, cache,
            )
            freeze_findings(record, default_artifact_dir(req.req_id))
            results.append(score_layer1(record, rubric, match_value=sm.coverage.match_value))

    # A mini-shortlist preview: ranked by base score, and NOBODY dropped —
    # ineligible/gate-blocked candidates appear with their reasons (§6.3).
    results.sort(key=lambda r: r.base_score, reverse=True)
    typer.echo(f"Rubric v{rubric.version} — Layer 1 (pure code) over {len(results)} candidates:\n")
    typer.echo(f"{'rank':<5}{'base':>6}  {'coverage':>8}  {'eligible':>8}  {'gate':>5}  candidate")
    for rank, r in enumerate(results, start=1):
        gate = "pass" if passes_gate(r) else "stop"
        typer.echo(
            f"{rank:<5}{r.base_score:>6.1f}  {r.coverage_score:>8.4f}  "
            f"{str(r.eligible):>8}  {gate:>5}  {r.candidate_id}"
        )
        for reason in r.reasons:
            typer.echo(f"{'':19}- {reason}")


@debug_app.command("l2")
def debug_l2(
    limit: int = typer.Option(3, "--limit", help="How many candidates."),
    jurisdiction: str = typer.Option(None, "--jurisdiction", help="Policy code (default from config.yaml)."),
) -> None:
    """Layer-2 adjustments for gate-passing candidates: signal, points, evidence,
    rationale — plus every code-enforcement action (drops/clamps)."""
    from datetime import date
    from pathlib import Path

    from resume_screener.ats import get_ats
    from resume_screener.azure_clients import get_document_intelligence_client, get_openai_client
    from resume_screener.cache import JsonCache
    from resume_screener.config import ConfigError, api_keys_for, load_app_config, load_settings
    from resume_screener.embed import clamped_cosine01, embed_texts
    from resume_screener.features import compute_features
    from resume_screener.findings import compute_findings
    from resume_screener.ingest import extract_file, parse_profile
    from resume_screener.layer1 import passes_gate, score_layer1
    from resume_screener.layer2 import run_layer2
    from resume_screener.policy import PolicyError, resolve_policy
    from resume_screener.redact import redact_text
    from resume_screener.registry import RegistryError, apply_policy, load_registry
    from resume_screener.rubric import RubricError, latest_rubric_path, load_rubric
    from resume_screener.scoring_model import ScoringModelError, load_scoring_model

    try:
        settings = load_settings()
        cfg = load_app_config()
        sm = load_scoring_model()
        keys = api_keys_for(settings)
        client_info = get_openai_client(settings, keys)
        policy = resolve_policy(jurisdiction or cfg.policy.default_jurisdiction, cfg.policy.policies_dir)
        registry = load_registry(cfg.signals_file)
        enabled_signals, excluded = apply_policy(registry, policy)
    except (ConfigError, PolicyError, RegistryError, ScoringModelError) as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)

    ats = get_ats(cfg)
    reqs = ats.fetch_requisitions()
    if not reqs:
        typer.echo(f"No JDs found in {cfg.ats.jd_dir}/.")
        raise typer.Exit(1)
    req = reqs[0]
    rubric_path = latest_rubric_path(req.jd_ref)
    if rubric_path is None:
        typer.echo(f"No rubric for {req.jd_ref} — run: screener rubric --jd {req.jd_ref}")
        raise typer.Exit(1)
    try:
        rubric = load_rubric(rubric_path)
    except RubricError as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)

    candidates = ats.fetch_candidates(req)[:limit]
    di = get_document_intelligence_client(settings, keys)
    cache = JsonCache(cfg.runtime.cache_dir)
    jd_text = Path(req.jd_ref).read_text(encoding="utf-8")
    today = date.today()
    typer.echo(
        f"Jurisdiction {policy.jurisdiction} — {len(enabled_signals)} enabled signals, "
        f"total cap ±{sm.adjustments.total_cap_points:g}"
    )

    shown_fully = False
    for cand in candidates:
        for doc in ats.fetch_documents(cand):
            extraction = extract_file(di, cfg.models.document_model, Path(doc.uri), cache,
                                      _extraction_for(cfg))
            redaction = redact_text(extraction.text, extraction.candidate_id, doc.file_name)
            profile, _ = parse_profile(
                redaction.redacted_text, extraction.candidate_id, doc.uri,
                client_info.client, settings.azure_openai_chat_deployment, cache,
            )
            record, _ = compute_findings(
                redaction.redacted_text, extraction.candidate_id, rubric,
                client_info.client, settings.azure_openai_chat_deployment, cache,
            )
            layer1 = score_layer1(record, rubric, match_value=sm.coverage.match_value)
            if not passes_gate(layer1):
                # Gate-blocked candidates never reach Layer 2 (§5.4) — that's the
                # cost model AND the design: their story is Layer 1's reasons.
                typer.echo(f"\ncandidate {layer1.candidate_id}: GATE STOP — {'; '.join(layer1.reasons)}")
                continue

            jd_vec, resume_vec = embed_texts(
                [jd_text, redaction.redacted_text],
                client_info.client, settings.azure_openai_embedding_deployment, cache,
            )
            features = compute_features(
                profile, rubric, policy, as_of=today,
                domain_fit_cosine=round(clamped_cosine01(jd_vec, resume_vec), 4),
            )
            result, notes, cached = run_layer2(
                profile, record, features, enabled_signals, excluded, jd_text,
                total_cap=sm.adjustments.total_cap_points, jurisdiction=policy.jurisdiction,
                client=client_info.client, model=settings.azure_openai_chat_deployment, cache=cache,
            )
            net = sum(a.points for a in result.adjustments)
            header = (
                f"candidate {result.candidate_id}: base {layer1.base_score:.1f}, "
                f"{len(result.adjustments)} adjustment(s), net {net:+.1f} "
                f"[{'cache' if cached else 'model'}]"
            )
            if not shown_fully:
                typer.echo(f"\n=== {header} ===")
                for a in result.adjustments:
                    typer.echo(f"  {a.points:+5.1f}  {a.signal_id}")
                    typer.echo(f'         evidence: "{a.evidence}"')
                    typer.echo(f"         rationale: {a.rationale}")
                if result.proposed_signals:
                    for p in result.proposed_signals:
                        typer.echo(f"  proposal (logged, never scored): {p.name} — {p.why}")
                if notes:
                    typer.echo("  enforcement: " + "; ".join(notes))
                typer.echo(f"  narrative: {result.narrative}")
                shown_fully = True
            else:
                summary_bits = [f"{a.points:+.1f} {a.signal_id}" for a in result.adjustments] or ["no adjustments"]
                typer.echo(f"\n{header}\n  " + "; ".join(summary_bits))
                if notes:
                    typer.echo("  enforcement: " + "; ".join(notes))


@debug_app.command("critic")
def debug_critic(
    limit: int = typer.Option(3, "--limit", help="How many candidates."),
    jurisdiction: str = typer.Option(None, "--jurisdiction", help="Policy code (default from config.yaml)."),
) -> None:
    """Critic verdicts per adjustment + the score effect of suspensions."""
    from datetime import date
    from pathlib import Path

    from resume_screener.ats import get_ats
    from resume_screener.azure_clients import get_document_intelligence_client, get_openai_client
    from resume_screener.cache import JsonCache
    from resume_screener.config import ConfigError, api_keys_for, load_app_config, load_settings
    from resume_screener.critic import apply_critic, four_fifths_monitor, run_critic
    from resume_screener.embed import clamped_cosine01, embed_texts
    from resume_screener.features import compute_features
    from resume_screener.findings import compute_findings
    from resume_screener.ingest import extract_file, parse_profile
    from resume_screener.layer1 import passes_gate, score_layer1
    from resume_screener.layer2 import run_layer2
    from resume_screener.policy import PolicyError, resolve_policy
    from resume_screener.redact import redact_text
    from resume_screener.registry import RegistryError, apply_policy, load_registry
    from resume_screener.rubric import RubricError, latest_rubric_path, load_rubric
    from resume_screener.scoring_model import ScoringModelError, load_scoring_model

    try:
        settings = load_settings()
        cfg = load_app_config()
        sm = load_scoring_model()
        keys = api_keys_for(settings)
        client_info = get_openai_client(settings, keys)
        policy = resolve_policy(jurisdiction or cfg.policy.default_jurisdiction, cfg.policy.policies_dir)
        registry = load_registry(cfg.signals_file)
        enabled_signals, excluded = apply_policy(registry, policy)
    except (ConfigError, PolicyError, RegistryError, ScoringModelError) as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)

    ats = get_ats(cfg)
    reqs = ats.fetch_requisitions()
    if not reqs:
        typer.echo(f"No JDs found in {cfg.ats.jd_dir}/.")
        raise typer.Exit(1)
    req = reqs[0]
    rubric_path = latest_rubric_path(req.jd_ref)
    if rubric_path is None:
        typer.echo(f"No rubric for {req.jd_ref} — run: screener rubric --jd {req.jd_ref}")
        raise typer.Exit(1)
    try:
        rubric = load_rubric(rubric_path)
    except RubricError as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)

    candidates = ats.fetch_candidates(req)[:limit]
    di = get_document_intelligence_client(settings, keys)
    cache = JsonCache(cfg.runtime.cache_dir)
    jd_text = Path(req.jd_ref).read_text(encoding="utf-8")
    today = date.today()
    marks = {"pass": "✓", "flag": "⚑", "suspend": "⛔"}

    for cand in candidates:
        for doc in ats.fetch_documents(cand):
            extraction = extract_file(di, cfg.models.document_model, Path(doc.uri), cache,
                                      _extraction_for(cfg))
            redaction = redact_text(extraction.text, extraction.candidate_id, doc.file_name)
            profile, _ = parse_profile(
                redaction.redacted_text, extraction.candidate_id, doc.uri,
                client_info.client, settings.azure_openai_chat_deployment, cache,
            )
            record, _ = compute_findings(
                redaction.redacted_text, extraction.candidate_id, rubric,
                client_info.client, settings.azure_openai_chat_deployment, cache,
            )
            layer1 = score_layer1(record, rubric, match_value=sm.coverage.match_value)
            if not passes_gate(layer1):
                typer.echo(f"\ncandidate {layer1.candidate_id}: GATE STOP — no Layer 2, nothing to critique")
                continue
            jd_vec, resume_vec = embed_texts(
                [jd_text, redaction.redacted_text],
                client_info.client, settings.azure_openai_embedding_deployment, cache,
            )
            features = compute_features(
                profile, rubric, policy, as_of=today,
                domain_fit_cosine=round(clamped_cosine01(jd_vec, resume_vec), 4),
            )
            layer2, _, _ = run_layer2(
                profile, record, features, enabled_signals, excluded, jd_text,
                total_cap=sm.adjustments.total_cap_points, jurisdiction=policy.jurisdiction,
                client=client_info.client, model=settings.azure_openai_chat_deployment, cache=cache,
            )
            from resume_screener import cascade
            from resume_screener.scoring_model import merge_extra_exclusions

            review, cached = run_critic(
                layer2, enabled_signals,
                (merge_extra_exclusions(sm.topics, policy.additional_exclusions)
                 + cascade.blocked_patterns_as_topics(policy.blocked_patterns)),
                client_info.client, settings.azure_openai_chat_deployment, cache,
            )
            surviving, suspended = apply_critic(layer2, review)
            net_before = sum(a.points for a in layer2.adjustments)
            net_after = sum(a.points for a in surviving)
            typer.echo(
                f"\n=== candidate {layer2.candidate_id}: base {layer1.base_score:.1f} | "
                f"L2 net {net_before:+.1f} -> after critic {net_after:+.1f} "
                f"[{'cache' if cached else 'model'}] ==="
            )
            for a in layer2.adjustments:
                verdict = review.verdict_per_adjustment.get(a.signal_id, "pass")
                typer.echo(f"  {marks[verdict]} {a.points:+5.1f}  {a.signal_id}  [{verdict}]")
            for note in review.notes:
                typer.echo(f"      note: {note}")
            if suspended:
                typer.echo("  suspended (removed from score, kept in record + UI queue): "
                           + ", ".join(a.signal_id for a in suspended))
            if review.overall_flags:
                typer.echo("  overall flags: " + "; ".join(review.overall_flags))

    monitors = four_fifths_monitor(policy.monitoring_categories)
    typer.echo(f"\nPool monitors ({policy.jurisdiction}): "
               + (", ".join(m.category for m in monitors) if monitors else "no categories configured"))
    for m in monitors:
        typer.echo(f"  - {m.category}: {m.note}")


def resolve_requisition(rows, jd_path, req_id: str | None = None):
    """Which requisition is this run for? By ID when given, else by JD path.

    Raises ValueError with a recruiter-readable message when the answer is not
    unambiguous, because guessing here screened the WRONG role on UAT: two
    requisitions pointed at the same jds/*.md, `next()` returned the first, and a
    run started for the 235-candidate requisition screened the other one's 6
    résumés. A requisition's identity is its id; a file path is not an identity.

    Extracted from `run` so it can be tested without the Azure preamble (settings,
    Key Vault, clients) that the command does first.
    """
    from pathlib import Path as _Path

    if req_id:
        match = next((r for r in rows if r.req_id == req_id), None)
        if match is None:
            raise ValueError(f"No requisition {req_id!r} is visible through the ATS port.")
        return match
    matches = [r for r in rows if _Path(r.jd_ref) == _Path(jd_path)]
    if len(matches) > 1:
        ids = ", ".join(m.req_id for m in matches)
        raise ValueError(f"{len(matches)} requisitions share {jd_path} ({ids}) — "
                         "pass --req <id> to say which one to screen.")
    if not matches:
        raise ValueError(f"{jd_path} is not visible through the ATS port.")
    return matches[0]


@app.command()
def run(
    jd: str = typer.Option(..., "--jd", help="Path to the JD (markdown/text)."),
    req: str = typer.Option(None, "--req", help="Requisition id to screen (the exact role; use when several share a JD)."),
    resumes: str = typer.Option(None, "--resumes", help="Explicit resumes folder (default: this role's batch under data/roles/)."),
    out: str = typer.Option(..., "--out", help="Output folder for reports + artifacts."),
    rubric_file: str = typer.Option(None, "--rubric", help="Rubric YAML (default: latest for the JD)."),
    jurisdiction: str = typer.Option(None, "--jurisdiction", help="Job-location code (default from config.yaml)."),
    limit: int = typer.Option(None, "--limit", help="Only the first N resumes (use 10 before a full run)."),
    concurrency: int = typer.Option(None, "--concurrency", help="Parallel candidates (default from config.yaml)."),
    no_redact: bool = typer.Option(False, "--no-redact", help="DANGER: skip identity masking (guardrail default ON)."),
    no_prefs: bool = typer.Option(False, "--no-prefs", help="Skip the HR-preference nudge stage (P40)."),
) -> None:
    """The full §5 pipeline: ingest -> redact -> parse -> findings -> Layer 1 ->
    features -> Layer 2 (gate-passers) -> critic -> final scores -> reports.

    Resumable by design: every model call is cached by content hash, so an
    interrupted or repeated run only pays for what it hasn't seen before."""
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import date, datetime, timezone
    from pathlib import Path

    from tqdm import tqdm

    from resume_screener.ats import get_ats
    from resume_screener.azure_clients import (
        METER,
        get_document_intelligence_client,
        get_openai_client,
    )
    from resume_screener.cache import JsonCache, content_hash
    from resume_screener.config import (
        ConfigError, api_keys_for, effective_extraction, load_app_config, load_settings,
    )
    from resume_screener.critic import run_critic
    from resume_screener.embed import clamped_cosine01, embed_texts
    from resume_screener.features import compute_features
    from resume_screener.findings import compute_findings, freeze_findings
    # The non-scoring distance lens. THIS IMPORT WAS MISSING and the worker still
    # referenced the module, so every candidate died with NameError at the "location"
    # stage — 235 of 235 on the pilot batch, i.e. screening was completely broken from
    # the moment the lens shipped. The import existed, but inside a DIFFERENT command's
    # function, so nothing in this one could see it. See test_no_undefined_names.py:
    # the real fix is that an undefined name is now a failing test, because this code
    # path needs Azure and no unit test ever executes it.
    from resume_screener import location_context
    from resume_screener.ingest import (
        PROFILE_PROMPT_VERSION, NotASingleResumeError, extract_file, extraction_summary,
        freeze_profile, parse_profile, single_resume_problem,
    )
    from resume_screener.localextract import LOCAL_EXTRACT_VERSION
    from resume_screener.findings import FINDINGS_PROMPT_VERSION
    from resume_screener.layer1 import passes_gate, score_layer1
    from resume_screener.layer2 import LAYER2_PROMPT_VERSION, run_layer2
    from resume_screener.critic import CRITIC_PROMPT_VERSION
    from resume_screener.policy import PolicyError, resolve_policy
    from resume_screener.redact import RedactionResult, record_candidate, redact_text
    from resume_screener.registry import RegistryError, apply_policy, load_registry
    from resume_screener.report import CandidateBundle, estimate_cost_usd, rank_bundles, write_reports
    from resume_screener.rubric import RubricError, latest_rubric_path, load_rubric
    from resume_screener.scoring import assemble_score
    from resume_screener.pref_match import PREFS_PROMPT_VERSION, run_pref_match
    from resume_screener.scoring_model import (
        ScoringModelError, load_scoring_model, merge_extra_exclusions,
        scoring_model_stamp,
    )

    # --- setup: config, registry, rubric, clients (policy resolved after we know the role) -
    try:
        settings = load_settings()
        cfg = load_app_config()
        sm = load_scoring_model()  # the governed scoring model (bands, caps, credit)
        cfg.ats.jd_dir = str(Path(jd).parent)
        keys = api_keys_for(settings)
        client_info = get_openai_client(settings, keys)
        registry = load_registry(cfg.signals_file)
    except (ConfigError, RegistryError, ScoringModelError) as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)
    # Where résumé text is read (2026-10-14) — resolved ONCE so every file in the run is
    # read the same way and run_meta can say which way that was.
    extraction_cfg, extraction_source, extraction_problem = effective_extraction(cfg)

    jd_path = Path(jd)
    if not jd_path.exists():
        typer.echo(f"✗ JD not found: {jd_path}")
        raise typer.Exit(1)
    rubric_path = Path(rubric_file) if rubric_file else latest_rubric_path(jd_path)
    if rubric_path is None or not Path(rubric_path).exists():
        typer.echo(f"✗ No rubric for {jd_path} — run: screener rubric --jd {jd_path}")
        raise typer.Exit(1)
    try:
        rubric = load_rubric(rubric_path)
    except RubricError as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)

    ats = get_ats(cfg, resumes_override=resumes)
    # Identify the requisition by ID when we were given one.
    #
    # Matching on the JD PATH alone is ambiguous and it bit hard on UAT: two
    # requisitions pointed at the same jds/*.md, so `next()` returned the first and a
    # run launched for the 235-candidate req screened the OTHER req's 6 résumés,
    # writing the results into the first req's output folder. Every candidate the
    # recruiter had just uploaded was silently skipped. A requisition's identity is
    # its id; a file path is not an identity, and the API always knows the id.
    try:
        req = resolve_requisition(ats.fetch_requisitions(), jd_path, req)
    except ValueError as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)
    # The requisition's PLACE, for the non-scoring distance lens (2026-08-26). Resolved
    # once per run and passed into the worker: computing it at screening time is what
    # lets Review render distances without re-reading a single résumé (that used to cost
    # 26s of PDF parsing per page load on the Azure Files share). Empty = no location on
    # the requisition, and the whole lens simply stays off.
    job_place = (req.work_location.place if req.work_location else "") or ""

    # Jurisdiction precedence (P24): explicit --jurisdiction > the role's stored job
    # location (the ATS read it from the role's sidecar) > the config default (legacy roles).
    code = (jurisdiction or req.job_location or cfg.policy.default_jurisdiction).strip()
    try:
        policy = resolve_policy(code, cfg.policy.policies_dir)
        enabled_signals, excluded = apply_policy(registry, policy)
    except PolicyError as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(1)
    req.job_location = code
    # The merged never-score list the critic enforces: the scoring model's topics
    # (phrases + stages) + the pack's additions (phrase-less, model-enforced) +
    # this jurisdiction's scoring/narrative-staged blocked patterns (the region
    # floor, evidence-screened on Layer-2 output — "jurisdiction supersedes").
    from resume_screener import cascade
    never_score_topics = (merge_extra_exclusions(sm.topics, policy.additional_exclusions)
                          + cascade.blocked_patterns_as_topics(policy.blocked_patterns))

    candidates = ats.fetch_candidates(req)
    if limit:
        candidates = candidates[:limit]
    if not candidates:
        typer.echo("✗ No resumes for this role — add them on the Screen page (Candidates), "
                   "or pass --resumes <folder>.")
        raise typer.Exit(1)

    redact_on = cfg.redaction.enabled and not no_redact
    out_dir = Path(out)
    artifact_dir = out_dir / "artifacts"
    jd_text = jd_path.read_text(encoding="utf-8")
    as_of = date.today()
    run_id = f"{req.req_id}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    workers = max(1, concurrency or cfg.runtime.concurrency)
    cache = JsonCache(cfg.runtime.cache_dir)
    di = get_document_intelligence_client(settings, keys)

    # --- P40: whose HR preferences apply to this requisition -------------------------
    # The requisition's sidecar contacts — created_by + the assigned HM — contribute
    # their active/approved preferences (never pending/blocked/declined/expired), so
    # the applied set is replayable from the sidecar + the preference log. --no-prefs
    # forces the stage off (then a run is byte-identical to pre-P40).
    applicable_prefs: list[dict] = []
    if not no_prefs:
        from resume_screener.ats.local_folder import load_req_meta
        from resume_screener import preferences

        sidecar = load_req_meta(jd_path)
        contacts: list[str] = []
        if sidecar.get("created_by"):
            contacts.append(sidecar["created_by"])
        hm = sidecar.get("hm")
        if isinstance(hm, dict) and hm.get("email"):
            contacts.append(hm["email"])
        seen_pref_ids: set[str] = set()
        for owner in contacts:
            for p in preferences.list_prefs(owner=owner):
                if preferences.applies(p) and p["pref_id"] not in seen_pref_ids:
                    seen_pref_ids.add(p["pref_id"])
                    applicable_prefs.append(
                        {"pref_id": p["pref_id"], "owner": p["owner"], "text": p["text"]})

    # --- the jurisdiction badge + standing caveats (§5.0) --------------------------
    typer.echo(f"Run {run_id} — {req.role_title}")
    typer.echo(
        f"Jurisdiction: {policy.jurisdiction} (pack {policy.pack} + base floor) · "
        f"{len(enabled_signals)}/{len(registry.signals)} signals enabled · "
        f"{len(policy.obligations)} obligations · rubric v{rubric.version}"
    )
    typer.echo(
        "Caveat: policies follow the JOB location. Candidate-residence rules for remote "
        "requisitions (e.g. NYC LL144, GDPR) are a production feature (§12)."
    )
    if not redact_on:
        typer.echo("⚠ REDACTION IS OFF (--no-redact): identity reaches the models. "
                   "Internal evaluation only; do not use for real screening.")
    if extraction_cfg.mode == "hybrid":
        typer.echo(f"Extraction: hybrid (from {extraction_source}) — résumés that carry their "
                   "own text are read here and redacted before any Azure AI service; only "
                   "scans and photos go to Document Intelligence.")
    else:
        typer.echo(f"Extraction: di-only (from {extraction_source}) — every résumé goes to "
                   "Document Intelligence before redaction.")
    if extraction_problem:
        typer.echo(f"⚠ {extraction_problem}")

    METER.reset()
    started = time.perf_counter()

    # --- per-candidate pipeline (runs in worker threads; caches make it resumable) --
    def process(doc) -> tuple[CandidateBundle | None, tuple[str, str], dict | None, dict | None]:
        stage = "extract"
        try:
            extraction = extract_file(di, cfg.models.document_model, Path(doc.uri), cache,
                                      extraction_cfg)
            ext_stat = {  # per-document extraction telemetry -> run_meta["extraction"] (P19)
                "from_cache": extraction.from_cache,
                "extractor": extraction.extractor,
                "route": extraction.route,
                "confidence": extraction.extraction_confidence,
            }
            stage = "validate"
            # Refuse to score a file that isn't a single candidate's résumé (an
            # un-split Bulk Print, a merged packet). Better a flagged error than a
            # meaningless score for a multi-candidate blob.
            problem = single_resume_problem(extraction.text, extraction.page_count)
            if problem:
                raise NotASingleResumeError(problem)
            # NON-SCORING location read (2026-08-26). Deliberately here: it uses the
            # ORIGINAL text, because redaction below strips exactly the address lines it
            # needs, and it is placed before every scoring stage so it is obvious that
            # nothing downstream consumes it. It produces kilometres and nothing else —
            # no band, no weight, no adjustment. See location_context's docstring for
            # why `commute_or_address` being a floor topic forces this shape.
            stage = "location"
            location = location_context.read_context(extraction.text, job_place)

            stage = "redact"
            if redact_on:
                redaction = redact_text(extraction.text, extraction.candidate_id, doc.file_name)
            else:  # explicit opt-out (§7); same shape so downstream code is identical
                redaction = RedactionResult(
                    candidate_id=extraction.candidate_id, redacted_text=extraction.text, counts={}
                )
            stage = "profile"
            profile, _ = parse_profile(
                redaction.redacted_text, extraction.candidate_id, doc.uri,
                client_info.client, settings.azure_openai_chat_deployment, cache,
            )
            stage = "findings"
            record, _ = compute_findings(
                redaction.redacted_text, extraction.candidate_id, rubric,
                client_info.client, settings.azure_openai_chat_deployment, cache,
            )
            stage = "layer1"
            layer1 = score_layer1(record, rubric, match_value=sm.coverage.match_value)

            layer2 = review = features = None
            enforcement: list[str] = []
            pref_adjustments: list = []
            if passes_gate(layer1):  # §5.4: Layer 2 + critic on gate-passers only
                stage = "features"
                jd_vec, resume_vec = embed_texts(
                    [jd_text, redaction.redacted_text],
                    client_info.client, settings.azure_openai_embedding_deployment, cache,
                )
                features = compute_features(
                    profile, rubric, policy, as_of=as_of,
                    domain_fit_cosine=round(clamped_cosine01(jd_vec, resume_vec), 4),
                )
                stage = "layer2"
                layer2, enforcement, _ = run_layer2(
                    profile, record, features, enabled_signals, excluded, jd_text,
                    total_cap=sm.adjustments.total_cap_points,
                    jurisdiction=policy.jurisdiction,
                    client=client_info.client, model=settings.azure_openai_chat_deployment,
                    cache=cache,
                )
                stage = "critic"
                review, _ = run_critic(
                    layer2, enabled_signals, never_score_topics,
                    client_info.client, settings.azure_openai_chat_deployment, cache,
                )
                # P40: the bounded preference nudge — gate-passers only, only when
                # the requisition has applicable preferences (else no model call).
                if applicable_prefs:
                    stage = "preferences"
                    pref_adjustments, _ = run_pref_match(
                        profile, record, applicable_prefs,
                        client_info.client, settings.azure_openai_chat_deployment, cache,
                        pref_cap=sm.preferences.per_preference_cap_points,
                        blocked_patterns=policy.blocked_patterns,  # per-req jurisdiction floor
                    )

            stage = "score"
            downgraded = sum(1 for f in record.findings if "Downgraded" in (f.note or ""))
            score = assemble_score(
                layer1, layer2, review,
                role_title=req.role_title, jurisdiction=policy.jurisdiction,
                extraction_confidence=extraction.extraction_confidence,
                downgraded_findings=downgraded,
                band_strong=sm.bands.strong_at, band_possible=sm.bands.possible_at,
                cap_band_if_must_have_missing=sm.bands.cap_if_must_have_missing,
                preference_adjustments=pref_adjustments,
                preference_cap=sm.preferences.per_preference_cap_points,
                confidence_rules=sm.confidence,
                clamp_min=sm.clamp.min, clamp_max=sm.clamp.max,
            )
            stage = "freeze"
            freeze_profile(profile, artifact_dir)
            freeze_findings(record, artifact_dir)
            bundle = CandidateBundle(
                score=score, findings=record, features=features,
                layer2_enforcement=enforcement,
                location=location,
                extraction_confidence=extraction.extraction_confidence,
                extractor=extraction.extractor,
                route=extraction.route,
                extraction_stats=extraction.stats,
                # DI pages billed by THIS run: none for a local read or a cache hit
                pages=(extraction.page_count
                       if extraction.extractor == "document-intelligence"
                       and not extraction.from_cache else 0),
            )
            return bundle, (extraction.candidate_id, doc.uri), ext_stat, None
        except Exception as exc:  # keep the run alive; one bad file != dead run
            return None, ("", ""), None, {
                "file": doc.file_name, "stage": stage,
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            }

    documents = [doc for cand in candidates for doc in ats.fetch_documents(cand)]
    bundles: list[CandidateBundle] = []
    map_entries: list[tuple[str, str]] = []
    ext_stats: list[dict] = []
    errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process, doc): doc for doc in documents}
        for future in tqdm(as_completed(futures), total=len(futures), desc="candidates", unit="cand"):
            bundle, map_entry, ext_stat, error = future.result()
            if error:
                errors.append(error)
            if ext_stat:
                ext_stats.append(ext_stat)
            if bundle:
                bundles.append(bundle)
                map_entries.append(map_entry)
            # Machine-readable per-candidate line for the UI job runner, so the
            # Candidates tab can mark rows screened/screening while the run is live.
            tag = "candidate-error" if error else "candidate-done"
            typer.echo(f"[{tag}] {futures[future].file_name}")

    # Private map writes are read-modify-write on one JSON file — done here in
    # the main thread, not the workers, to avoid racing.
    for candidate_id, uri in map_entries:
        record_candidate(candidate_id, uri, out_dir)

    # --- run_meta: the audit stamp (§5.7) -------------------------------------------
    meter = METER.snapshot()
    di_pages = sum(b.pages for b in bundles)
    gate_passers = sum(1 for b in bundles if b.score.layer2 is not None)
    suspended_total = sum(
        1 for b in bundles if b.score.critic
        for v in b.score.critic.verdict_per_adjustment.values() if v == "suspend"
    )
    flagged_total = sum(
        1 for b in bundles if b.score.critic
        for v in b.score.critic.verdict_per_adjustment.values() if v == "flag"
    )
    bands = {band: sum(1 for b in bundles if b.score.fit_band == band)
             for band in ("Strong", "Possible", "Not a fit")}
    # Extraction visibility (P19; local-first since 2026-10-14): which résumés were read
    # here, which went to Document Intelligence and why, and what came from cache.
    extraction_stats = extraction_summary(ext_stats, extraction_cfg.mode)
    run_meta = {
        "run_id": run_id,
        "role_title": req.role_title,
        "jd_ref": str(jd_path),
        "as_of": as_of.isoformat(),
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_seconds": round(time.perf_counter() - started, 1),
        "jurisdiction": policy.jurisdiction,
        "policy_pack": policy.pack,
        "floor": policy.floor,
        "obligations": policy.obligations,
        "monitoring_categories": policy.monitoring_categories,
        "keep_at_least_days": policy.keep_at_least_days,
        "purge_by_days": policy.purge_by_days,
        "policy_notes": policy.notes,
        "remote_req_caveat": "Policies follow the job location; candidate-residence "
                             "policy union for remote reqs is a production feature (§12).",
        "redaction_enabled": redact_on,
        "enabled_signals": [s.id for s in enabled_signals],
        "excluded_signals": [e.id for e in excluded],
        "versions": {
            "app": "resume-screener 0.1.0",
            "chat_deployment": settings.azure_openai_chat_deployment,
            "embedding_deployment": settings.azure_openai_embedding_deployment,
            "document_model": cfg.models.document_model,
            # How résumé text was read (2026-10-14): the mode and every input that decides
            # what a local read produces. Not a governed stamp — a mode change does not
            # make a decided requisition "stale".
            "extraction": {
                "mode": extraction_cfg.mode,
                "source": extraction_source,
                "local_min_chars_per_page": extraction_cfg.local_min_chars_per_page,
                "local_rules": LOCAL_EXTRACT_VERSION,
                "pypdf": _package_version("pypdf"),
                "python_docx": _package_version("python-docx"),
            },
            "prompts": {
                "profile": PROFILE_PROMPT_VERSION, "findings": FINDINGS_PROMPT_VERSION,
                "layer2": LAYER2_PROMPT_VERSION, "critic": CRITIC_PROMPT_VERSION,
            },
            "rubric": f"{Path(rubric_path).name} (v{rubric.version})",
            # The governed scoring model these scores were assembled under — since
            # the 2026-07-18 merge it also carries the signals + never-scored topics
            # (the old separate registry stamp is gone with the registry file).
            "scoring_model": scoring_model_stamp(),
            "policy_files": {
                "base": content_hash(Path(cfg.policy.policies_dir, 'base.yaml').read_bytes())[:12],
                policy.pack: content_hash(
                    Path(cfg.policy.policies_dir, f"{policy.pack}.yaml").read_bytes())[:12],
            },
            # P40: which HR preferences were in scope (replayability). Empty when the
            # stage was off (--no-prefs) or the requisition has no applicable prefs.
            "preferences": {
                "applied": [
                    {"pref_id": p["pref_id"], "owner": p["owner"],
                     "text_hash": content_hash(p["text"])[:12]}
                    for p in applicable_prefs
                ],
                "prompt": PREFS_PROMPT_VERSION,
            },
        },
        "counts": {
            "resumes_in": len(documents), "scored": len(bundles),
            "gate_passers": gate_passers, "gate_blocked": len(bundles) - gate_passers,
            "adjustments_suspended": suspended_total, "adjustments_flagged": flagged_total,
            "preference_adjustments": sum(len(b.score.preference_adjustments) for b in bundles),
            "errors": len(errors),
        },
        "bands": bands,
        "extraction": extraction_stats,
        "cost_estimate": {
            **meter, "di_pages_analyzed": di_pages,
            "estimated_usd": estimate_cost_usd(meter, di_pages),
            "note": "Approximate list prices — cached steps cost nothing; verify against Azure billing.",
        },
        "errors": errors,
    }

    paths = write_reports(out_dir, bundles, rubric, run_meta)

    # --- run index (Phase 4): also stamp a Run row when the DB backend is on ----------
    # The file (run_meta.json + reports + per-candidate JSON) is the source of truth;
    # this is an additive INDEX write, best-effort so a DB hiccup never fails a run
    # whose reports already landed on disk.
    from resume_screener.config import load_db_settings

    if load_db_settings().run_index_backend.strip().lower() == "db":
        try:
            from resume_screener.db.repositories import regions as regions_repo
            from resume_screener.db.repositories import runs as runs_repo
            from resume_screener.db.session import session_scope

            with session_scope() as session:
                region_id = regions_repo.region_id_for_jurisdiction(session, policy.jurisdiction)
                runs_repo.record_run(
                    session, run_meta, req_code=req.req_id, region_id=region_id,
                    finished_at=datetime.now(timezone.utc),
                )
            typer.echo("Run indexed in the database.")
        except Exception as exc:  # never fail a completed run over the index write
            typer.echo(f"⚠ Could not index this run in the database "
                       f"({type(exc).__name__}: {str(exc).splitlines()[0][:120]}); reports are on disk.")

    # --- console summary --------------------------------------------------------------
    # "met every must-have" = passed the hard gates AND has no missing must-have
    # (passes_gate), so it earned the Layer-2 + critic pass. This is a NARROWER
    # set than "passed the hard gates" (which the Review page counts as
    # gate-passers) — a candidate who clears every hard gate but misses a
    # must-have is a gate-passer in Review yet is NOT counted here. The wording
    # deliberately describes the criterion instead of a stage ("advanced" read as
    # if the rest could be ignored — everyone stays listed for review).
    typer.echo(f"\nScored {len(bundles)}/{len(documents)} resumes in {run_meta['duration_seconds']}s "
               f"({gate_passers} met every must-have; {len(errors)} errors).")
    typer.echo(f"Bands: Strong {bands['Strong']} · Possible {bands['Possible']} · "
               f"Not a fit {bands['Not a fit']} · suspended adj {suspended_total} · flagged {flagged_total}")
    typer.echo(f"Jurisdiction stamp: {policy.jurisdiction} (pack {policy.pack}) — "
               f"{len(policy.obligations)} obligations recorded in run_meta.json")
    typer.echo(f"Estimated cost: ${run_meta['cost_estimate']['estimated_usd']} "
               f"({meter['chat_calls']} chat calls, {meter['embedding_calls']} embedding calls, "
               f"{di_pages} DI pages — cached steps free)")
    typer.echo(f"Extraction ({extraction_stats['mode']}): "
               f"{extraction_stats['documents_processed']} documents — "
               f"{extraction_stats['local_extractions']} read here, "
               f"{extraction_stats['di_documents']} by Document Intelligence "
               f"({extraction_stats['di_calls']} new calls), "
               f"{extraction_stats['cache_hits']} from cache, "
               f"{extraction_stats['fallback_extractions']} fallback")
    typer.echo("\nTop of the list:")
    for rank, bundle in enumerate(rank_bundles(bundles)[:5], start=1):
        s = bundle.score
        typer.echo(f"  {rank}. {s.candidate_id}  {s.fit_band:<10} final {s.final_score:5.1f} "
                   f"(base {s.layer1.base_score:5.1f}) conf {s.confidence}")
    for error in errors:
        typer.echo(f"  ⚠ {error['file']} failed at {error['stage']}: {error['error']}")
    typer.echo(f"\nReports: {paths['xlsx']} · {paths['csv']} · {paths['html']}")
    typer.echo(f"Audit:   {paths['run_meta']} · per-candidate JSON in {paths['candidates_dir']}")


@app.command()
def ui(
    port: int = typer.Option(8000, "--port", help="Port for the review UI / API."),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (0.0.0.0 in a container)."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev)."),
) -> None:
    """Serve the recruiter review UI — the FastAPI app plus the built React
    frontend (web/dist). Run `npm run build` in web/ first, or use the Vite dev
    server (`npm run dev`) against this API during development."""
    import uvicorn

    uvicorn.run("resume_screener.api.app:app", host=host, port=port, reload=reload,
                server_header=False)  # don't name the server software (2026-10-14)


@app.command()
def mcp(
    blind: bool = typer.Option(False, "--blind/--named",
                               help="Blind mode: candidates appear as 'Candidate N' "
                                    "and contact lookups are disabled. Default: named."),
) -> None:
    """Serve the Ask-Claude MCP connector on stdio (Claude Desktop / Claude Code).

    Read-only: nine permission-checked tools over the same review surfaces the
    web app serves, every call logged to the audit trail. Requires the
    'ask_claude_mcp' feature flag (Feature-flags page). Identity comes from
    DEV_USER / DEV_ROLE locally — never from the conversation."""
    from resume_screener.api.mcp import run_stdio

    run_stdio(blind=blind)


if __name__ == "__main__":
    app()
