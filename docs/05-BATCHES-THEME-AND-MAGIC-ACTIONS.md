# ADDENDUM 05 — Resume Batches per Role, Acme Theme, "Magic" AI Actions (P18–P21)

Covers: (1) whether Document Intelligence is actually used and how to verify it, (2) per-role resume batch management with upload/import UX, (3) viewing the original resume per candidate, (4) distinct styling for AI-triggering ("expensive") actions, (5) the Acme design system applied to the UI.

---

## Part A — Yes, Document Intelligence is used. Here's where, and why you can't see it.

> **Superseded 2026-10-14 — P21 is built, as the default.** Résumés that carry their own text (digital PDFs, Word files) are now read locally (`localextract.py`) and redacted before any Azure AI service sees them; only scans and photos go to Document Intelligence. The motive changed from cost to privacy (InfoSec question 6). `EXTRACTION_MODE=di-only` restores the behaviour described below. See the CLAUDE.md banner "INFOSEC CLOSE-OUT".

**Where:** `ingest.py` (built in P2). Every resume (PDF/DOCX) is sent to the `ResumeScreener` Document Intelligence resource with the `prebuilt-layout` model to extract its text — that text is what redaction, parsing, findings and scoring all run on. It's the very first paid call in the pipeline.

**Why it's invisible:** two reasons. First, results are **cached by file content hash** — DI fires exactly once per unique resume file, ever; every re-run and every screen after the first is a cache hit, so you'll never see it "working" again for the same files. Second, nothing in the UI surfaces it (fixed in P19: extraction stats land in Admin → Runs).

**Verify it yourself in two minutes:** (a) look in `.cache/` — the extraction entries are there, keyed by hash; (b) Azure portal → the `ResumeScreener` resource → **Metrics → Transactions/Calls** — you'll see the spike from your first run; (c) `uv run screener check` — check #3 makes a live DI call every time.

**Is it earning its cost?** For born-digital PDFs, a plain local parser could extract text for free. DI earns its keep on **scanned/photographed resumes** (very common in India hiring), complex layouts, tables, and consistent DOCX handling — plus the per-field confidence signal that feeds the "hard to read" banner. P21 (optional) adds a hybrid mode that tries free local extraction first and calls DI only when the local text yield is poor — typically cuts DI cost 60–80% on a mostly-digital corpus with zero quality loss.

---

## Part B — Per-role resume batches (the UX fix for "4 resumes found… from where?")

**Today's problem:** one global `data/resumes/` folder that you populate by hand with no instructions, shared across all roles, invisible provenance, no add/remove.

**The design:** every role owns its candidate batch:
```
data/roles/<role-slug>/resumes/     ← the batch for that role
```
Managed entirely from the UI — nobody touches folders manually anymore:

- **Upload** (primary path): drag-and-drop multi-file uploader (PDF/DOCX) right on the Screen page → files saved into the role's batch.
- **Import from folder** (bulk path): paste a folder path (e.g., your OneDrive-synced local folder) → the app **copies** the files in. Copy, not reference, deliberately: the project stays self-contained, OneDrive paths going offline can't break screening, deletion/retention has a single story, and duplicates are caught on the way in (same content hash = same resume even if renamed — common when candidates are forwarded twice).
- **Manage**: a file table (name, size, date added) with per-file remove and a "Clear batch" button (confirm dialogs).
- **Provenance everywhere**: "Screening **42 candidates** for **Finance Manager — Mumbai** (this role's batch)".

**Multiple roles, overlapping candidates:** fine by design — each role's batch is independent (matching how an ATS scopes candidates to a requisition), and because the extraction/LLM cache is keyed by *content*, the same resume appearing in two batches costs nothing extra to process the second time.

**Production alignment bonus:** `LocalFolderATS.fetch_candidates(req)` now reads the per-role folder — exactly the shape of the future iCIMS/Greenhouse adapter fetching candidates per requisition. This change moves the PoC *closer* to production, not further.

---

## Prompts

### P18 — Per-role resume batches + batch manager UI

```
Restructure candidate storage to per-role batches, per Addendum 05 Part B (read it in the repo
root first). Engine contracts unchanged.

1. Storage: data/roles/<role-slug>/resumes/ per role (slug from role title). One-time migration:
   if legacy data/resumes/ has files, offer in the UI to assign them to a chosen role (copy) or
   leave them; don't silently move.
2. ats/local_folder.py: fetch_candidates(req) reads that requisition's batch folder. Keep the
   ATSPort interface unchanged.
3. Screen page becomes role-scoped with a "Candidates" manager ABOVE the screen button:
   - drag-and-drop multi-file uploader (pdf/docx) -> saves into the role's batch;
   - "Import from folder": text input for a local folder path + Import button -> COPY files in,
     recursively, pdf/docx only; dedupe by content hash (report "12 added, 3 duplicates
     skipped, 1 unsupported"); friendly errors for bad paths/permissions;
   - batch table: filename, size, added date, remove button per file; "Clear batch" with
     confirm; live count.
   - provenance line: "Screening N candidates for {role} (this role's batch)".
4. Review page and reports read from the role's outputs as before; candidate source paths in
   the private map now point into the role folder.
5. Update CLAUDE.md §3 (structure) and §7 (Screen page) to match. Add tests for slug generation,
   dedupe-on-import, and the ATS port reading per-role folders.
Run the app, demonstrate upload + folder import + remove on a test role, show me the results, stop.
```

### P19 — View/download the original resume + extraction visibility

```
Two additions, per Addendum 05:
1. REVIEW detail panel: a "View original resume" download button per candidate, serving the
   source file bytes via the private candidate_id -> source_file map, plus a small caption with
   the file name and its path (with a copy-to-clipboard affordance). In Blind-review mode this
   control is hidden and replaced by a note "hidden during blind review" (revealing the file
   would defeat blinding).
2. Extraction visibility (answers "is Document Intelligence used?"): record per run in
   run_meta.json: documents processed, DI calls made vs cache hits, fallback extractions, and
   the extraction-confidence distribution. Surface these in Admin -> Runs as a small
   "Extraction" stats block per run.
Run the app, show both working (normal + blind mode), stop.
```

### P20 — Acme design system + "magic" styling for AI actions

```
Apply the Acme design system to the Streamlit app, per Addendum 05 Part C (the token table
below is authoritative — from Acme's official corporate template). Presentation layer only.

1. Theme (.streamlit/config.toml): primaryColor #008943, backgroundColor #FFFFFF,
   secondaryBackgroundColor #F4F4F4, textColor #222222, font sans-serif.
2. Global CSS (injected once from a ui_theme.py helper): font-family "Bio-Type", Arial,
   sans-serif (Bio-Type applies only if the org's webfont files are added later — Arial is the
   official fallback); body text #222222 (never #000000); captions/footers #797979; links
   #40AFFF; card/border gray #E4E4E4; highlighted panels #F0F2E6 (light green tint); a slim
   mint header band (#F0F2E6) across the top of the app with the app name — and if
   assets/acme-logo.svg exists in the repo, show it there (do NOT fabricate a logo; if the
   file is absent, plain text wordmark).
3. Semantic colors: Strong match chip = #008943 bg / white text; Possible match = #FFE354 bg /
   #222222 text; Not a match = #E4E4E4 bg / #797979 text; "needs a second look" banners =
   #FFE354 tint. Score bars in #008943.
4. MAGIC AI ACTIONS — any button whose click triggers AI/LLM or Document Intelligence calls
   ("Create screening checklist", "Screen resumes", future re-screen) gets a distinct treatment:
   ✨ prefix in the label, a green gradient fill (#00A651 -> #008943) with white text and a soft
   glow on hover (CSS targeting these buttons via a shared key/class convention), and a tooltip:
   "Uses AI — costs apply. Already-screened resumes re-run free (cached)." All non-AI actions
   (approve, decisions, uploads, deletes, exports) use standard styling. Add a small legend in
   the sidebar: "✨ = uses AI".
5. Keep recruiter-mode copy rules from Addendum 04 intact.
Run the app, screenshot/describe each page, confirm the AI-action styling is visually unmistakable, stop.
```

### P21 — Optional: hybrid extraction to cut Document Intelligence cost

*(Built 2026-10-14 as the default, for privacy rather than cost — see the note at the top of Part A. Two refinements over the prompt below: pypdf's plain mode rather than pdfplumber, and the routing counts pages by kind — a file goes to DI when image-only pages outnumber text pages, when the whole file has under one page of text, or when the text layer is mostly junk.)*

```
Optional cost optimization, engine behavior unchanged in output shape: in ingest.py, try a free
local text extraction first (pypdf/pdfplumber for PDF, python-docx for DOCX). If the local text
yield is good (e.g., > a configurable chars-per-page threshold and sane word ratio), use it and
mark extractor="local"; otherwise call Document Intelligence prebuilt-layout as today and mark
extractor="document-intelligence". Record the split in run_meta (surfaced in the P19 stats).
Add config: extraction: { mode: hybrid|di-only, local_min_chars_per_page: 200 }. Default hybrid.
Tests for the routing logic. Show me the stats split on the sample batch, stop.
```

---

## Part C — Acme design tokens (authoritative, from the corporate template)

| Token | Value | Use in the app |
|---|---|---|
| Primary green | `#008943` | Primary buttons, Strong-match chips, score bars, focus states |
| Bright brand green | `#00A651` | "Magic" AI-action gradient start; brand accents |
| Body text | `#222222` | All body text — **never `#000000`** |
| Caption gray | `#797979` | Captions, footers, secondary text, Not-a-match text |
| Light gray | `#E4E4E4` | Borders, dividers, Not-a-match chip bg |
| Light bg | `#F4F4F4` | Secondary background (sidebar, table stripes) |
| Green tint | `#F0F2E6` | Highlighted cards, the top header band |
| Yellow highlight | `#FFE354` | Possible-match chips, attention banners |
| Link blue | `#40AFFF` | Hyperlinks only |
| Blue accent | `#90D8E6` | Sparing informational accents (ⓘ) |
| Headings font | Bio-Type → Arial fallback | Bio-Type webfont only if the org supplies files; Arial is officially sanctioned fallback |
| Code font | Courier New | Admin/debug views only |

Rules carried from the brand: white text on any solid `#00A651` surface; palette is closed (no colors outside this table); body text never pure black.

---

**Sequence:** P18 → P19 → P20 (each is independent, but this order avoids re-styling screens you're about to change). P21 whenever cost matters. And your point #6 came through empty — send it over whenever ready.
