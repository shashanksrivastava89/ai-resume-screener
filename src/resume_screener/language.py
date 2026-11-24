"""Which language is this document written in? (global rollout, 2026-09-12)

WHY THIS EXISTS. `service.jd_language` counted characters above U+024F and called
anything under 20% "English". Hungarian á é í ó ö ő ú ü ű are all below that
point, so a job description written entirely in Hungarian scored 0% and got no
"non-English JD" badge at all — and so did German, French, Spanish and Polish. The
function detected NON-LATIN SCRIPT and was named for something else; it was built
for Devanagari and never revisited.

HOW THIS WORKS. Function words. Every language leans on a small closed set of
them ("the", "and", "of" / "a", "és", "hogy"), they are the most frequent tokens
in any real text, and they are short enough to list. Count how many of a
document's tokens are function words of each candidate language; the winner takes
it if it is ahead by a clear margin. Non-Latin script short-circuits to
"non_english" as before, because a stop-word list in Latin letters can say nothing
about Devanagari or Han.

DELIBERATELY SMALL AND DETERMINISTIC. This is a display hint — it puts a badge on
a card and names a language in the intake note. It never reaches a score, so a
library (and a dependency, and a model call) would be the wrong trade. A document
we cannot place returns None, which renders as no badge: the honest answer.
"""

from __future__ import annotations

import re

# The most frequent function words per language. Chosen to be distinctive where
# possible — "és"/"hogy" (hu) do not appear in German, "und"/"der" (de) do not
# appear in Hungarian — and kept short, because the winner is decided by a margin,
# not by a score anyone reads.
_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset("""the and of to in for with a an is are on as by be will you
        your our we they this that from at or not have has""".split()),
    "hu": frozenset("""a az és hogy nem is de vagy egy meg ki be el van volt lesz
        már csak még ha mint így ezen ezt amely valamint illetve""".split()),
    "de": frozenset("""der die das und in von zu mit für den dem ein eine ist sind
        auf als auch im nicht bei oder sie wir uns aus über""".split()),
    "fr": frozenset("""le la les de des et en un une du au aux pour dans par sur
        avec est sont vous nous que qui ne pas plus ou""".split()),
    "es": frozenset("""el la los las de del y en un una para por con que se es son
        su sus como más o no al lo""".split()),
    "it": frozenset("""il lo la i gli le di del della e in un una per con che non
        sono è come più o al da""".split()),
    "nl": frozenset("""de het een en van in op te met voor zijn is worden aan door
        bij dat die niet of ook als uit""".split()),
    "pl": frozenset("""i w na do z nie że się o jak po lub oraz jest są dla przez
        od przy który która które""".split()),
    "pt": frozenset("""o a os as de do da dos das e em um uma para por com que não
        se é são mais ou no na""".split()),
    "ro": frozenset("""și de la în cu pe un o care este sunt pentru din nu se al
        ale sau ca mai să şi""".split()),
    "cs": frozenset("""a v na se je pro do od s z že které který která jsou být
        nebo při po jako již""".split()),
}

#: The names these codes carry on screen. English is absent on purpose — an
#: English document gets no badge.
LANGUAGE_NAMES: dict[str, str] = {
    "hu": "Hungarian", "de": "German", "fr": "French", "es": "Spanish",
    "it": "Italian", "nl": "Dutch", "pl": "Polish", "pt": "Portuguese",
    "ro": "Romanian", "cs": "Czech", "en": "English",
    "non_english": "non-English",
}

_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)
#: Below this many tokens the counts are noise, not evidence.
_MIN_TOKENS = 25
#: The winner must hold this share of the function-word hits. Two Romance
#: languages share many short words, so a narrow win is not a finding.
_MARGIN = 1.35


def detect(text: str) -> str | None:
    """The document's language as an ISO 639-1 code, "non_english" for a non-Latin
    script, or None when the text is too short or too evenly split to call.

    None is a real answer, not a failure: no badge is better than a wrong one."""
    if not text:
        return None
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return None
    # Non-Latin script: a Latin stop-word list can say nothing useful about it.
    # (The original heuristic, kept for exactly the case it was built for.)
    if sum(1 for c in letters if ord(c) > 0x024F) / len(letters) > 0.2:
        return "non_english"

    tokens = [t.casefold() for t in _TOKEN.findall(text)]
    if len(tokens) < _MIN_TOKENS:
        return None
    scores = {code: sum(1 for t in tokens if t in words)
              for code, words in _STOPWORDS.items()}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, best_score = ranked[0]
    if best_score < 3:
        return None
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if runner_up and best_score < runner_up * _MARGIN:
        return None
    return best


def name_of(code: str | None) -> str:
    """"hu" -> "Hungarian". The code itself when we have no name for it."""
    if not code:
        return ""
    return LANGUAGE_NAMES.get(code, code)
