# ADDENDUM 08 — Host for the HR Team, SSO + RBAC, US Extension, Editable Rules, UX Fixes (P22–P28)

> **Status (2026-07-11): P22–P28 are all implemented.** P26–P28 were done against the
> **FastAPI + React** app (Addendum 09 Part F), not Streamlit, so where this document
> says Streamlit / port **8501** / `streamlit run`, the shipped reality is **uvicorn on
> port 8000** (`Dockerfile` builds `web/dist` in a node stage; CMD =
> `uvicorn resume_screener.api.app:app --host 0.0.0.0 --port 8000`). The Part B admin
> block below has been corrected to the real port. Everything else — Easy Auth, app
> roles, the RBAC matrix, DATA_ROOT on Azure Files — is as designed here.

Covers items: readability contrast (P22) · guided journey after role creation (P23) · US/Concord extension via per-role job location (P24) · fully editable rules & policies with governance rails + RBAC gating (P25) · hosting on Azure so HR can self-test (P26 + admin steps) · SSO with Entra ID and the four roles (P27) · README regeneration (P28).

**The strategic point:** hosting and SSO solve each other. Deploying to Azure Container Apps gives you a URL, **Managed Identity** (the `az login` → production identity swap the code was designed for — zero code change for Key Vault), and **built-in Entra ID authentication** ("Easy Auth") — SSO with essentially no code. SSO then supplies the user identity that RBAC (item 5) and the decision audit trail need. So: yes, the time for SSO is now.

---

## Part A — Target hosted architecture (PoC-grade, production-shaped)

```
HR user (browser) ── company login (Entra ID, tenant ef0aebeb…) ──►
Azure Container Apps  "resume-screener"  (Central India, 1 replica)
  ├─ built-in auth (Easy Auth): only tenant users; app roles → RBAC
  ├─ System Managed Identity ──► Key Vault (ReesumeScreenerKV): reads the 2 API keys
  ├─ calls ──► Azure OpenAI (eastus resource, Global Standard deployments) + Document Intelligence (eastus; scans only since 2026-10-14)
  └─ volume mount ──► Azure Files share  = DATA_ROOT
        (jds/, data/roles/…, output/, signals/, policies/, .cache/ — same layout as local)
```

Key choices, and why:
- **Container Apps** over App Service/VM: consistent with the production design (§12), built-in auth, deploy-from-source (`az containerapp up` builds in the cloud — no local Docker needed), cheap.
- **One replica (min=max=1), deliberately.** Streamlit sessions and our file-based storage are single-node-friendly; one replica + file locks avoids multi-writer complexity. Scale-out comes with the Postgres/Blob move, not before.
- **Azure Files mount = keep the flat-file design.** Container filesystems are wiped on every restart — without the mount you'd lose decisions and artifacts. The share persists everything under `DATA_ROOT` with the exact same folder layout, so nothing else changes.
- **Central India** for the app + storage (users are in India; aligns with the future data posture). The AI resources stay in eastus — cross-region latency is negligible next to LLM latency. *(Correction, 2026-10-14: the Azure OpenAI deployments are Global Standard, so prompts may be processed in any Azure region, not only East US; and since 2026-10-14 only scanned résumés reach Document Intelligence.)*
- **Managed Identity** replaces your laptop's `az login` in the cloud — `DefaultAzureCredential` picks it up automatically. This was the promised payoff of the Key Vault design: no secrets anywhere, no code change.
- Public URL, **gated by tenant login** (standard for internal PoCs). If IT requires network-private instead, that's a VNet/internal-ingress variant — an IT decision, not a redesign.

**RBAC model (extendible):** four Entra **app roles** — `HRHead`, `IT`, `TASpecialist`, `HRSpecialist` — assigned to people by the admin in the Enterprise Application. In-app mapping lives in `rbac.yaml` (editable):

| Capability | HRHead | IT | TASpecialist | HRSpecialist |
|---|---|---|---|---|
| Recruiter mode (Roles/Screen/Review, decisions) | ✓ | ✓ | ✓ | ✓ |
| Admin → Rules & Policies (edit/publish) | ✓ | view | — | — |
| Admin → Runs & Audit | ✓ | ✓ | — | — |
| Admin → Data (delete cache / candidate data) | ✓ | ✓ | — | — |

**Cost:** roughly $20–40/month (Container Apps consumption at 1 small replica + Azure Files + registry). **Effort:** Cloud Admin ~45 minutes; prompts P22–P28 ≈ one focused vibe-coding day.

---

## Part B — Who does what

**You (with Claude Code):** run P22 → P28 in order. P22–P25 are pure app work (do these first so testers meet the improved UX). P26 prepares the app for containers; P27 wires SSO/RBAC; P28 refreshes the README.

**Cloud Admin (after P26 exists in the repo):** the block below. Placeholders in ⟨⟩; storage-account names must be globally unique lowercase.

```bash
az login --tenant 00000000-0000-4000-8000-2200910f4d4b
az account set --subscription 00000000-0000-4000-8000-b361bdfa266c
az extension add --name containerapp --upgrade

# 1) Persistent storage (Central India)
az storage account create -n ⟨resumescreenerpoc⟩ -g GIT-Infra-DevOps-Dev-Resource -l centralindia --sku Standard_LRS
az storage share-rm create --storage-account ⟨resumescreenerpoc⟩ -n appdata --quota 64

# 2) Build + deploy from source (run in the repo root; uses the P26 Dockerfile —
#    FastAPI/uvicorn on 8000, per Addendum 09; NOT the Streamlit-era 8501)
az containerapp up -n resume-screener -g GIT-Infra-DevOps-Dev-Resource -l centralindia \
  --ingress external --target-port 8000 --source .

# 3) Managed Identity -> Key Vault
az containerapp identity assign -n resume-screener -g GIT-Infra-DevOps-Dev-Resource --system-assigned
# take the printed principalId:
az role assignment create --assignee ⟨principalId⟩ --role "Key Vault Secrets User" \
  --scope $(az keyvault show -n ReesumeScreenerKV --query id -o tsv)

# 4) Mount the share + app settings + single replica
az containerapp env storage set -g GIT-Infra-DevOps-Dev-Resource \
  -n ⟨env name printed by step 2⟩ --storage-name appdata \
  --azure-file-account-name ⟨resumescreenerpoc⟩ --azure-file-share-name appdata \
  --azure-file-account-key ⟨key⟩ --access-mode ReadWrite
# add the volume + mount (path /data) to the app — P26 ships a ready containerapp.patch.yaml:
az containerapp update -n resume-screener -g GIT-Infra-DevOps-Dev-Resource --yaml containerapp.patch.yaml
az containerapp update -n resume-screener -g GIT-Infra-DevOps-Dev-Resource \
  --min-replicas 1 --max-replicas 1 \
  --set-env-vars DATA_ROOT=/data \
    AZURE_KEY_VAULT_URI=https://your-keyvault.vault.azure.net/ \
    AZURE_OPENAI_API_KEY_SECRET=azure-openai-api-key \
    DOCUMENTINTELLIGENCE_API_KEY_SECRET=document-intelligence-api-key \
    AZURE_OPENAI_ENDPOINT=https://your-openai.openai.azure.com/ \
    AZURE_OPENAI_CHAT_DEPLOYMENT=⟨your chat deployment⟩ \
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT=⟨your embedding deployment⟩ \
    DOCUMENTINTELLIGENCE_ENDPOINT=https://your-docintel.cognitiveservices.azure.com/

# 5) SSO (portal is easiest): Container App -> Authentication -> Add identity provider ->
#    Microsoft -> create new app registration -> this tenant only -> "Require authentication".
# 6) Roles: Entra ID -> App registrations -> (the app just created) -> App roles ->
#    create HRHead, IT, TASpecialist, HRSpecialist (value = same string) ->
#    Enterprise applications -> the app -> Users and groups -> assign people to roles.
# 6b) v2 (Addendum 11): ALSO create two more app roles — HiringManager, SuperAdmin —
#     and add the Container App env var FEATURE_HM_PORTAL (unset/0 = HM portal dark;
#     1 = on). Full step + capability matrix: Addendum 11 Part B.
```

> **v2 update (2026-07-13):** the RBAC model is now **six** app roles — the four above **plus `HiringManager` and `SuperAdmin`** — and one new env var **`FEATURE_HM_PORTAL`** (dark-launches the hiring-manager portal). The Cloud-Admin step for both is spelled out in **[Addendum 11 Part B](11-V2-REQUISITION-WORKSPACE.md)** (it extends this Part B); capabilities are the union across the roles a person holds.

Exact flags occasionally drift — the admin (or you) can paste any error straight into Claude Code, which is fully capable of driving `az`. **Post-deploy checklist:** URL loads and demands company login → sidebar shows your name + role → Admin hidden for a TASpecialist test user → upload → ✨ screen → results survive an app restart (proves the mount) → decision recorded with your email as reviewer → logs visible via `az containerapp logs show -n resume-screener -g GIT-Infra-DevOps-Dev-Resource --follow`.

---

## Part C — The prompts

### P22 — Readability / contrast pass (brand ≠ low contrast)

```
Accessibility pass on the UI, presentation-layer only. Several places use light grey text on
white/near-white — unreadable. Enforce WCAG AA (contrast >= 4.5:1 for text, 3:1 for large text)
across the app while staying inside the Acme palette (hues unchanged; neutrals may darken —
accessibility adjustments to neutrals are brand-permitted):
- body text ALWAYS #222222; secondary/supporting text no lighter than #5A5A5A on white/#F4F4F4;
  #797979 reserved for true footers/captions only; NEVER use #E4E4E4/#F0F2E6/tints as text color.
- Fix known offenders: "Not a match" chip (use #4A4A4A text on #E4E4E4, or white on #797979),
  disabled/placeholder states, table secondary columns, expander labels, captions on tinted cards.
- Audit systematically: grep every color declaration in ui_theme.py/CSS/config.toml, compute the
  contrast ratio of each text/background pair, print a before/after table of every pair you
  changed, and assert none is below AA.
Run the app, confirm visually page by page, show me the audit table, stop.
```

### P23 — Guided journey: per-role stepper + dynamic next action (dashboard stays)

```
Fix the "what do I do next?" problem, per Addendum 08. Keep the Roles page as a DASHBOARD and add
a guided, wizard-like flow PER ROLE — do not force a modal wizard.

1. Role lifecycle state machine, computed from artifacts on disk (no new state files):
   Draft checklist -> Checklist approved -> Candidates added -> Screened -> In review -> Complete.
   ("In review" = results exist with undecided Strong/Possible candidates; "Complete" = all
   Strong/Possible have decisions, or the user presses "Mark complete".)
2. Role detail: a horizontal stepper showing the six stages with the current one highlighted,
   and ONE primary CTA that changes with state: "Approve checklist" -> "Add candidates" ->
   "✨ Screen resumes" -> "Review N candidates" -> "Mark complete". ✨ styling only where AI runs.
   Secondary actions (edit checklist, manage batch) available but visually secondary.
3. After "Create role": land directly in the role's flow at the checklist step with a success
   toast "Role created — next: review and approve the screening checklist".
4. Empty states everywhere with one-line guidance + the CTA (e.g., Screen page with 0 candidates:
   "No candidates yet — upload files or import a folder ⤵").
5. Roles dashboard: each card shows a stage chip + mini progress ("42 screened · 18 reviewed")
   and the same dynamic CTA; header shows aggregate metrics (active roles, candidates screened,
   awaiting review) across all roles.
Run the app, walk a brand-new role end-to-end narrating what the UI told you to do at each step,
stop.
```

### P24 — Per-role job location + activate the US packs (Concord)

```
Extend beyond India, per Addendum 08. The policy engine already ships US packs — wire them in:
1. Role create/edit gains a Job location field: country select (India, United States, Singapore,
   Japan, EU country, Other); if United States -> state select; if California -> jurisdiction
   US-CA; if New York -> "Within New York City?" toggle -> US-NYC else US-default; other states
   -> US-default. Store on the Requisition (job_location); the per-role effective policy replaces
   reliance on the global default (config default remains the fallback for legacy roles).
2. Review the US packs' obligations text: US-NYC keeps LL144 (bias audit + 10-business-day
   candidate notice); US-CA adds "CCPA/CPRA notice at collection for applicants" and a note on
   California's civil-rights (CRD) automated-decision rules; US-default = generic EEOC/Title VII
   +ADA line. Keep them plain-language.
3. Screen/Review captions and run_meta reflect the ROLE's jurisdiction ("Hiring rules applied:
   US — California ⓘ"); Admin -> Rules shows which pack each role resolves to.
4. Tests: the same role flipped IN -> US-CA changes enabled rules/params and the obligations
   list; floor unchanged in both.
Create a demo role "Sr. Finance Analyst — Concord, CA (US)" and show me its Screen page caption
and effective rules vs an India role. Stop.
```

### P25 — Fully editable Rules & Policies (HR Head) with governance rails

```
Upgrade Admin -> Rules from on/off+weight to full configuration editing, per Addendum 08, with
governance rails so editability never breaks auditability:
1. RULES editor (registry.yaml): create / edit / retire rules. Editable per rule: friendly name,
   plain-language description AND the agent-facing definition text, direction (+/−/both), cap
   (Small/Medium/Large -> points), kind, default params. Creating a rule REQUIRES a
   job-relatedness rationale field. Retire = soft (kept in history, never applied).
2. POLICY editor per jurisdiction (policies/*.yaml): per-rule enable/disable + cap override +
   params (e.g., tenure thresholds), obligations checklist text (add/edit/remove lines),
   retention days, monitoring categories.
3. RAILS (non-negotiable): the base excluded list and floor items are LOCKED (🔒 shown; new
   exclusions may be ADDED, base ones never removed); every publish bumps the registry/pack
   version, records author (SSO identity when present, else DEV_USER), timestamp, a diff
   summary and the rationale to signals/audit_log.jsonl; existing floor-protection validation
   runs on save and blocks violations with a clear message; a persistent banner "Draft
   configuration — pending HR + Legal review" shows until an admin records the review
   (reviewers + date) in Admin.
4. RBAC hooks now, enforcement later: wrap pages with require_permission() reading rbac.yaml
   (roles HRHead/IT/TASpecialist/HRSpecialist -> capabilities per the Addendum 08 matrix);
   pre-SSO it reads DEV_ROLE env so we can test all four roles locally.
5. Runs continue to stamp the exact registry+pack versions used.
Demonstrate: edit a rule's cap, add a rule with rationale, try to remove a base exclusion (must
fail), switch DEV_ROLE=TASpecialist (Admin must disappear). Stop.
```

### P26 — Containerize + persistence + multi-user hardening

```
Prepare for Azure Container Apps hosting, per Addendum 08 Part A. App behavior unchanged locally.
1. DATA_ROOT env (default "."): ALL mutable paths (jds/, data/, output/, .cache/, signals/,
   policies/) resolve under it. First-run bootstrap: if DATA_ROOT is empty, seed it from the
   repo's committed defaults (registry, policy packs, config) — never overwrite existing files.
2. Dockerfile: python:3.11-slim, install uv + deps, copy app, EXPOSE 8501, healthcheck,
   CMD = streamlit run of the UI headless on 0.0.0.0:8501. Also ship containerapp.patch.yaml
   adding the Azure Files volume (storage name "appdata") mounted at /data.
3. Multi-user hardening for ONE replica: file locks (e.g., portalocker) around every write to
   decisions.jsonl, audit_log.jsonl, registry/pack YAML and the private map; atomic
   write-temp-then-rename for YAML saves.
4. Reviewer identity plumbing: a get_current_user() helper — returns the SSO identity when the
   auth headers exist (P27), else DEV_USER env, else "local-user"; Decision.reviewer and audit
   entries use it.
5. Build the image locally IF docker is available; otherwise validate the Dockerfile by
   inspection and rely on cloud build (az containerapp up --source). Update CLAUDE.md §§3/8/12
   for DATA_ROOT + deployment. Stop, and print the exact admin command block from Addendum 08
   Part B for me to forward.
```

### P27 — SSO (Entra ID Easy Auth) + RBAC enforcement

```
Wire authentication and roles, per Addendum 08. Easy Auth (configured by the admin on the
Container App) injects identity headers — the app consumes them; no auth SDK needed:
1. Parse X-MS-CLIENT-PRINCIPAL (base64 JSON of claims) -> user email/name + app roles
   (HRHead / IT / TASpecialist / HRSpecialist). Implement inside get_current_user().
2. Enforce rbac.yaml via require_permission() on every page/action per the Addendum 08 matrix:
   Recruiter mode = all four roles; Rules & Policies edit = HRHead (IT view-only); Runs & Audit
   = HRHead + IT; Data deletion = HRHead + IT. Unknown/no role -> friendly "access not
   configured — contact IT" page listing the four roles.
3. Sidebar user chip: name + role; Decisions and audit entries record the real email.
4. Local dev unchanged via DEV_USER/DEV_ROLE. Add tests for the header parser (sample principal
   payloads) and permission matrix. Update CLAUDE.md §7. Stop.
```

### P28 — Regenerate the README

```
Rewrite README.md as the front door of the repo, current with everything through Addendum 08:
- What it is (5 lines: two-layer engine, human decides, jurisdiction-aware, evidence-first).
- Quickstart LOCAL (az login, .env deployments, uv sync, screener check, screener ui) and
  HOSTED (the URL placeholder, who to ask for role assignment).
- The four CLI commands + the UI's Recruiter/Admin modes in two lines each.
- Where data lives (link Addendum 04), the docs index: 06-HR-TEAM-EXPLAINER (share with HR),
  07-TECH-EXPLAINER-AND-RUNBOOK (operations), ARCHITECTURE-TARGET-DESIGN (rationale).
- RBAC roles table; the "internal evaluation only until HR+Legal review" banner note;
  troubleshooting pointer (runbook §Troubleshooting).
Keep it under ~120 lines, plain language. Stop.
```

---

**Sequence & gates:** P22 → P23 → P24 → P25 (app is now demo-ready locally) → P26 → *admin runs Part B* → P27 → verify the post-deploy checklist → P28 → send HR the URL + `06-HR-TEAM-EXPLAINER.md`. Rollback is always available: the app keeps running locally exactly as before; hosting adds a copy, it doesn't move your ground truth (back up `output/` before migrating data to the share).
