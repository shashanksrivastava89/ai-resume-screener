# AI Resume Screener

An AI-assisted resume screening workbench for recruiters and hiring managers. It reads every resume against the job's actual requirements, explains each verdict with evidence, and keeps a human in charge of every decision.

> **About this repository.** Built for a Fortune-500 life-sciences company and running in production there after security review and change-advisory-board (CAB) approval. This public copy is anonymised: the client's name and branding are replaced with the placeholder "Acme", and every person, customer, product, price, address and identifier in the seed data is synthetic. Internal hostnames, cloud resource names, credentials and deployment runbooks have been removed. Some test fixtures that were exports of real business files were not published, so a few tests are expected to fail (listed under **Tests**).

## Outcomes in production

- **5,000+ resumes screened**
- **74 roles filled**, with average time-to-fill down from **6 weeks to 2 weeks**
- **20 active users** across recruiting and hiring managers
- Live in production after security review and CAB approval

## The problem

Recruiters were reading hundreds of resumes per requisition by hand. Shortlists were slow, inconsistent between reviewers, and hard to defend: nobody could say afterwards *why* one candidate moved forward and another did not. Hiring managers waited weeks for a first shortlist.

## What it does

- **Requisition workspace.** Turns a job description into a structured checklist of must-have and nice-to-have requirements that the recruiter can edit and approve.
- **Resume intake.** Accepts PDF/DOCX in bulk, extracts text with Azure AI Document Intelligence (with a local fallback), de-duplicates candidates by contact details, and detects re-applications.
- **Explainable scoring.** Scores each candidate against every requirement with a versioned scoring model (`signals/scoring_model.v*.yaml`), and records the evidence quoted from the resume for each verdict.
- **PII redaction and fairness guardrails.** Redacts names, contact details and ID numbers before the model sees a resume, and a critic pass checks scoring for bias-prone signals.
- **Human-in-the-loop decisions.** Bands such as *Strong*, *Possible* and *Not a fit* are suggestions only. A recruiter confirms or overrides each one, and every decision is written to an audit trail.
- **Ask-the-assistant handoff and MCP server.** Exposes requisitions and candidates to an LLM assistant through a Model Context Protocol server, with role-based access.
- **RBAC and SSO.** Role-based access (`rbac.yaml`) behind Microsoft Entra ID. Decision verdicts are encrypted at rest.

## Architecture

```
React (Vite) SPA  ──►  FastAPI  ──►  scoring pipeline ──► Azure OpenAI (structured outputs)
                         │              │
                         │              └─► Document Intelligence (extraction)
                         ├─► PostgreSQL (SQLAlchemy + Alembic)
                         ├─► MCP server (assistant tools)
                         └─► Key Vault (secrets via managed identity)
```

The deterministic core owns the rules: requirement parsing, de-duplication, redaction, banding thresholds and audit. The LLM is used for extraction and for scoring evidence, with schema-validated outputs and retries.

## Stack

Python 3.12, FastAPI, Pydantic, SQLAlchemy and Alembic, PostgreSQL, Azure OpenAI, Azure AI Document Intelligence, MCP, pandas, React 18, TypeScript, Vite, Vitest, Docker, Azure Container Apps, uv.

## Running locally (synthetic data)

```bash
# backend
uv sync --all-extras
cp .env.example .env          # leave the Azure values as placeholders to run with local extraction only
uv run pytest -q              # run the test suite

# web
cd web && npm install && npm run dev
```

`jds/example-data-analyst.md` is a synthetic requisition you can screen against. Bring your own sample resumes; none are shipped in this repository.

## Tests

| Suite | Result on this public copy |
|---|---|
| Python (pytest) | 1,774 passed, 3 failed, 1 skipped |
| Web (Vitest) | 376 / 376 passed |

The remaining Python failures also fail on the original code: an HTTP 404 vs 405 assertion and two security-header checks.

## License

MIT. See [LICENSE](LICENSE). More engineering detail is in [`docs/`](docs/) and [`docs/DEVELOPER-NOTES.md`](docs/DEVELOPER-NOTES.md).
