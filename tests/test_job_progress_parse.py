"""The screening-progress line parsers (jobs._PROGRESS + jobs._eta_seconds).

Regression guard: the run prints "11/12 signals enabled" at start-up, which used
to be misread as candidate progress ("Screening 11 of 12…") before the real
tqdm bar began. Only tqdm's bar (which carries the trailing "[elapsed<…]"
bracket) must be treated as progress. The remaining-time estimate comes from the
same bracket's "<remaining" part (None while tqdm shows "?").
"""

from __future__ import annotations

from resume_screener.api.jobs import _CANDIDATE, _PROGRESS, _eta_seconds


def test_signals_enabled_line_is_not_progress():
    assert _PROGRESS.search("11/12 signals enabled · 5 obligations · rubric v3") is None


def test_scored_summary_line_is_not_progress():
    # handled by the separate "Scored …" branch, not the progress regex
    assert _PROGRESS.search("Scored 12/12 resumes in 8.1s") is None


def test_tqdm_bar_is_progress():
    m = _PROGRESS.search("candidates:  58%|#####     | 7/12 [00:03<00:02,  2.1it/s]")
    assert m and (int(m.group(1)), int(m.group(2))) == (7, 12)


def test_tqdm_complete_bar_is_progress():
    m = _PROGRESS.search("candidates: 100%|##########| 12/12 [00:05<00:00,  2.4it/s]")
    assert m and (int(m.group(1)), int(m.group(2))) == (12, 12)


# --- the remaining-time estimate ----------------------------------------------------


def test_eta_minutes_seconds():
    assert _eta_seconds("candidates:  58%|#####     | 7/12 [00:03<00:05,  2.1it/s]") == 5


def test_eta_hours_minutes_seconds():
    line = "candidates:   2%|          | 9/500 [01:00<1:02:03,  7.6s/cand]"
    assert _eta_seconds(line) == 3723


def test_eta_unknown_is_none():
    assert _eta_seconds("candidates:   0%|          | 0/500 [00:00<?, ?it/s]") is None


def test_eta_absent_on_non_progress_lines():
    assert _eta_seconds("11/12 signals enabled · 5 obligations · rubric v3") is None
    assert _eta_seconds("Scored 12/12 resumes in 8.1s") is None


# --- per-candidate markers (extracted from anywhere in the line) --------------------
# tqdm's bar carries no trailing newline, so a marker printed right after a bar
# refresh arrives GLUED onto it — the markers must be found mid-line, not only at
# the start (the original startswith() lost every glued one, so chips never lit).


def test_candidate_marker_plain():
    assert _CANDIDATE.findall("[candidate-done] Anita Rao.pdf") == ["Anita Rao.pdf"]


def test_candidate_error_marker():
    assert _CANDIDATE.findall("[candidate-error] bob.pdf") == ["bob.pdf"]


def test_candidate_marker_glued_onto_tqdm_bar():
    line = "candidates:  15%|#         | 10/66 [00:12<01:07,  2.1it/s][candidate-done] Anita Rao.pdf"
    assert _CANDIDATE.findall(line) == ["Anita Rao.pdf"]
    # and the tqdm part is still readable once the marker is stripped
    stripped = _CANDIDATE.sub("", line).strip()
    m = _PROGRESS.search(stripped)
    assert m and (int(m.group(1)), int(m.group(2))) == (10, 66)
    assert _eta_seconds(stripped) == 67


def test_multiple_markers_glued_together():
    assert _CANDIDATE.findall("[candidate-done] a.pdf[candidate-error] b.pdf") == ["a.pdf", "b.pdf"]
