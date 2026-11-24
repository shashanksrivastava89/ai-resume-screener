# How the Resume Screener Works — A Guide for HR & Talent Acquisition

*Share this with anyone who will use or evaluate the tool. No technical background needed. (~5 minute read)*

---

## What it is, in one paragraph

The Resume Screener is an assistant that reads every resume submitted for a role, checks each one against **your screening checklist** (the must-haves and nice-to-haves you approved for that job), and hands you a ranked shortlist — with the **exact line from the resume quoted as proof** for every claim it makes. It recommends; **you decide**. It cannot reject anyone, and it never hides a candidate.

## What happens when you press "✨ Screen resumes"

1. **It reads each resume.** PDFs and Word files are turned into text — including scanned or photographed resumes.
2. **It hides personal details before judging.** Name, photo, contact details, age and gender are removed from what the scoring sees, so the evaluation is about the work, not the person. (You still see the candidate's file when you review — unless you switch on Blind review.)
3. **It checks your checklist.** Every must-have and nice-to-have is marked ✓ met, ~ partly met, or ✗ not found — each with a quote from the resume showing where. If it can't find proof, it says "not found"; it is not allowed to guess.
4. **It adds context notes — within strict limits.** A small set of pre-approved judgment rules (visible in Admin → Rules) can nudge a score up or down a few points — for example, "+ Relevant competitor experience" or "− Very short recent stints (no contract-work explanation)." Every nudge is capped, quoted, and explained. The rules are the *only* judgments it may make.
5. **A fairness check reviews the reasoning.** A second automated check reads every context note and sets aside anything that looks like it rests on an improper basis. Set-aside notes don't count toward the score and are listed for a human to look at.
6. **You review and decide.** You see the ranked list, open a candidate, read the evidence, and press Move forward / Don't move forward / Discuss — with a quick reason. That decision is yours, is recorded, and is the final word.

## What it will never do

- **Never rejects or advances anyone on its own.** Even a candidate missing every must-have stays on your list, labeled with the reasons.
- **Never considers**: employment gaps, being currently unemployed, age, gender, family status, address or commute, salary history, or how prestigious a school sounds. These are permanently off-limits — not settings.
- **Never invents evidence.** Every claim must quote the resume; unverifiable claims are dropped automatically.
- **Never learns on its own.** Your decisions are recorded so the tool can be improved later — but improvements are made offline, tested for fairness, and approved by a person before anything changes.

## Reading the results

- **Strong match / Possible match / Not a match** — the headline verdict. A candidate missing a must-have can never be rated above "Possible match," no matter how good the rest looks.
- **"Resume was hard to read — please double-check"** — the file was low quality (often a photo/scan); give this one extra human attention rather than trusting the score.
- **"Set aside by fairness check"** — notes the fairness reviewer excluded; they did not affect the score.
- **✨ on a button** means that action uses AI and costs money to run. Re-running already-screened resumes is free (results are remembered).

## Location-aware by design

Hiring rules differ by place. Each role carries its job location, and the tool applies that location's rule set — an India role and a Concord (California) role are screened under different obligations and judgment-rule settings, automatically. You'll see "Hiring rules applied: …" on the Screen page.

## Your data

Resumes and results stay inside the company's own Azure environment and the tool's secure storage — nothing goes to outside services beyond the company's Azure AI, and nothing is shared with model providers for training. Access to the app requires your company login, and the Admin pages are restricted to authorized roles.

## Quick FAQ

**Why can't the scoring see age, photo, or name?** Because they aren't evidence of ability — removing them is how we keep the first pass about the work.
**A clearly skilled person came out "Possible match" — why?** Usually a missing must-have (which caps the rating) or missing proof in the resume for a required item. Open the candidate: the ✗ rows tell you exactly what wasn't found — and you can still move them forward.
**Is the AI making hiring decisions?** No. It drafts a ranked, evidenced shortlist. Every advance/reject is a human action, recorded with a reason.
**What if I disagree with it?** Disagree! Pick your decision and a reason. Overrides are exactly the feedback that makes the next version better.
**Who controls the judgment rules?** The HR Head, in Admin → Rules — every change is versioned and logged, and the built-in fairness limits can't be switched off.

*One important note while we pilot:* the tool is in **evaluation mode** — its outputs inform testing and feedback only, and are not used to progress or reject candidates on live requisitions until the HR + Legal review is complete.
