# SETUP, AUTH & CONFIG v3 — Manual Steps (your real resources)

Unchanged in substance from v2 — Key Vault-first secrets, your existing resources — with v3's prompt numbers and the UI noted. No new manual steps were added by the v3 architecture (Streamlit is just a Python dependency).

---

## A. Your environment (confirmed)

| Resource | Name | Endpoint / URI | Status |
|---|---|---|---|
| Azure OpenAI | `ResumeScreenerOAI` (eastus, Standard) | `https://your-openai.openai.azure.com/` | ✓ exists — **two deployments needed (step C)** |
| Document Intelligence | `ResumeScreener` (eastus, S0) | `https://your-docintel.cognitiveservices.azure.com/` | ✓ exists — S0 right for ~500 resumes. (*API Kind "FormRecognizer" is the legacy ARM label — expected.*) |
| Key Vault | **`ReesumeScreenerKV`** | `https://your-keyvault.vault.azure.net/` | ✓ exists, **RBAC confirmed** — admin tasks: 2 secrets + 1 role (step D) |
| Tenant / Subscription | `00000000-0000-4000-8000-2200910f4d4b` / `00000000-0000-4000-8000-b361bdfa266c` | — | for `az login` / `az account set` |

> ⚠️ The vault name really is **`ReesumeScreenerKV`** (double "e"); it's baked in verbatim. Don't "fix" the spelling in `.env`. If your admin ever recreates it with the intended spelling, update `AZURE_KEY_VAULT_URI` only.

**Region note (India-first pilot):** your Azure resources sit in **eastus** while the first live roles are India-based. Under the DPDP Act 2023 cross-border processing is currently permitted (the US isn't a restricted destination), so **eastus is fine for the PoC**; for production, plan to stand up processing/storage in an Indian region (e.g., Central India) as future-proofing and for latency — a redeploy, not a redesign.

## B. Tools on your machine (one-time)
**Git**, **Python 3.11+**, **uv** (you have these from flywheel), plus: **Azure CLI** (`az --version`), **Claude Code** (primary builder), **Cursor** (editor; run Claude Code in its terminal). Corporate laptop: if installs or `az login` are blocked by policy/proxy, raise the IT ticket early.

## C. Create the two model deployments (~5 min)
Portal → `ResumeScreenerOAI` → **Azure AI Foundry portal** → **Deployments → Deploy model**:
1. A **GPT-4.1 / GPT-4o-class chat model** (newest non-preview offered; Standard/Global Standard). Name the deployment **the same as the model** and note it.
2. **`text-embedding-3-large`**, same approach.
Leave default TPM; if the 500-resume run hits 429s later, raising this quota is the durable fix.
→ the two names go into `.env` (`AZURE_OPENAI_CHAT_DEPLOYMENT`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`).

## D. Admin request (send as-is)
> **Subject:** Resume Screener PoC — Key Vault secrets + read access
>
> Hi — for the Resume Screener PoC (RG `GIT-Infra-DevOps-Dev-Resource`), per your guidance that keys come from Key Vault, on **`ReesumeScreenerKV`** (`https://your-keyvault.vault.azure.net/`) could you please:
> 1. (Access configuration is already **Azure RBAC** — confirmed, nothing to change here.)
> 2. Add two **secrets**: `azure-openai-api-key` = KEY 1 of `ResumeScreenerOAI`; `document-intelligence-api-key` = KEY 1 of `ResumeScreener`.
> 3. Grant my user (⟨your UPN⟩) the **Key Vault Secrets User** role on this vault.
> 4. Confirm the vault's **Networking** allows my machine/VPN (or add my network).
>
> *Optional (fully keyless later):* instead of (2), grant me **Cognitive Services OpenAI User** on `ResumeScreenerOAI` and **Cognitive Services User** on `ResumeScreener`.

## E. Sign in once
```
az login --tenant 00000000-0000-4000-8000-2200910f4d4b
az account set --subscription 00000000-0000-4000-8000-b361bdfa266c
```
This login is what `DefaultAzureCredential` uses at runtime — no keys ever sit in `.env`. Role grants can take a few minutes to propagate.

## F. `.env` (after P0 creates `.env.example`)
Copy to `.env`, fill only the two deployment TODOs. Everything is non-secret:
```
AZURE_KEY_VAULT_URI=https://your-keyvault.vault.azure.net/
AZURE_OPENAI_API_KEY_SECRET=azure-openai-api-key
DOCUMENTINTELLIGENCE_API_KEY_SECRET=document-intelligence-api-key
AZURE_OPENAI_ENDPOINT=https://your-openai.openai.azure.com/
AZURE_OPENAI_CHAT_DEPLOYMENT=⟨step C⟩
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=⟨step C⟩
DOCUMENTINTELLIGENCE_ENDPOINT=https://your-docintel.cognitiveservices.azure.com/
AZURE_TENANT_ID=00000000-0000-4000-8000-2200910f4d4b
AZURE_SUBSCRIPTION_ID=00000000-0000-4000-8000-b361bdfa266c
```

## G. Data from OneDrive
Resumes (PDF/DOCX) → `data/resumes/` — **3–5 files for P2–P10**, all ~500 only at **P11**. JDs (your India Finance & Sales roles) → paste text into `jds/role-1.md` etc. (or at P4 ask the AI to extend `rubric` to read PDF/DOCX JDs via the same extractor). Privacy: resumes stay in `data/` (gitignored) and your Azure region; delete `data/`, `output/`, `.cache/` when the PoC ends.

## H. First gate
After **P1**: `uv run screener check` → five ✓ (sign-in, KV secrets, Document Intelligence, chat, embeddings). ✗ maps to: E not done · D-3 role missing · D-4 network · wrong deployment name.

## I. Cost & run notes
- ≈ **5–6 model calls/resume**: DI + parse + findings + embedding, then Layer 2 + critic on **gate-passers only** (typically 40–60% of the pool) — the two-layer design is also a cost optimization.
- All calls cached by content hash → the first full run is the spend; re-runs, rubric edits and calibration are near-free.
- Always `--limit 10` (P11) before the full 500; read `run_meta.json` (tokens/cost/errors) before scaling.
- Persistent 429s → raise the deployment TPM quota (step C).
