"""PII redaction (P3, CLAUDE.md §6.2 — default ON).

How this fits the pipeline
--------------------------
extract (ingest.py) -> REDACT (this module) -> parse/findings/Layer 2 (models).
Everything a scoring model ever sees passes through redact_text() first. That is
the floor guarantee "redaction" in policies/base.yaml.

Why pure regex and not an LLM
-----------------------------
Redaction must complete BEFORE text reaches any model — using a model to redact
would leak the unredacted text to the very service we're shielding it from. So
this is deterministic, reviewable pattern matching: slightly conservative, fully
auditable, and testable offline.

What gets removed vs preserved
------------------------------
Removed  : name, email, phone, links, street address/postcode, photo references,
           gender, age/DOB, national identifiers, and the proxies résumés in
           several markets print as a matter of course — parents' and spouse's
           names, marital status, nationality, religion, caste/community.
Preserved: skills, employers, employment dates, education (incl. dates — an
           accepted trade-off; the critic polices age-proxy reasoning at scoring
           time), and job content generally.
Known limitation (PM decision, P3): city names on the contact line stay — cities
appear in legitimate work history ("Deloitte, Mumbai") and blanket masking would
destroy job content. Scoring on location is separately forbidden (excluded list).

GOING GLOBAL (2026-09-12) — what was broken and why it mattered
---------------------------------------------------------------
Two failures, both silent, both of which let identity through on exactly the
résumés the next pilots will send:

1. NAMES WERE ASCII-ONLY. `_name_tokens` split on ``[^A-Za-z]`` and the letterhead
   test was ``[A-Za-z.']+``, so "Kovács Péter" produced the fragments "Kov" and
   "ter" — and Python's ``\\b`` is Unicode-aware, so ``\\bKov\\b`` never matched
   inside "Kovács". The candidate's name reached Azure OpenAI intact, blind review
   showed a named candidate, and `counts["name_tokens"]` simply read 0. Every
   Hungarian, German, French, Spanish, Polish and Czech name was affected — most
   of EMEA and a large slice of California.

2. LABELS WERE ENGLISH-ONLY. A Hungarian résumé's "Születési dátum:" (date of
   birth), "Neme:" (gender) and "Családi állapot:" (marital status) matched
   nothing, so date of birth and gender reached the scoring model on an EU
   résumé — precisely the exposure policies/EU.yaml exists to prevent. A US
   résumé's SSN passed too: `_mask_number_runs` masks 10–13 digits and an SSN is
   nine.

Both are fixed here, in CODE, because redaction is a floor item: it is not a
jurisdiction's dial and cannot be turned down by a policy pack.

Editing tips
------------
- Add a new personal-detail label to _PII_LABELS under its category and language
  rather than inventing a new mechanism. The separator (``:``/``-``/``|``) after
  the label is MANDATORY and is what keeps ordinary words out: Hungarian "nem" is
  also the word "no", German "Alter" is inside "Alternative", French "genre"
  appears in "genre de contrat". A label at the start of a line with a colon
  after it is a field; the same letters in prose are not.
- Test any change with tests/test_redact.py — it pins "identity gone, job content
  intact" against an Indian, a Hungarian, a German and a US sample.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from pydantic import BaseModel

# Marker left where a personal-detail line was removed — visible in the UI so a
# reviewer can SEE that something was withheld (nothing is silently hidden).
REMOVED = "[removed: personal detail]"

# A Unicode LETTER, in any script. `[A-Za-z]` was the bug: it reads "Kovács" as
# two words and "Péter" as one letter short.
_L = r"[^\W\d_]"

# --- the personal-detail label table ------------------------------------------------
#
# category -> language -> the label forms that introduce it. Compiled into ONE
# line-level pattern; the separator after the label is added by the compiler and
# is what makes a short word like "nem" or "kor" safe to list.
_PII_LABELS: dict[str, dict[str, tuple[str, ...]]] = {
    "dob": {
        "en": (r"date\s+of\s+birth", r"d\.?\s*o\.?\s*b\.?", r"birth\s*date"),
        "hu": (r"születési\s+(?:idő|dátum|hely(?:e)?(?:\s+és\s+ideje)?|év)",
               r"szül\.?\s*(?:idő|dátum|hely)"),
        "de": (r"geburtsdatum", r"geburtstag", r"geboren\s+am", r"geburtsort"),
        "fr": (r"date\s+de\s+naissance", r"né\(?e\)?\s+le", r"lieu\s+de\s+naissance"),
        "es": (r"fecha\s+de\s+nacimiento", r"lugar\s+de\s+nacimiento"),
        "it": (r"data\s+di\s+nascita", r"luogo\s+di\s+nascita"),
        "nl": (r"geboortedatum", r"geboren\s+op", r"geboorteplaats"),
        "pl": (r"data\s+urodzenia", r"miejsce\s+urodzenia"),
        "pt": (r"data\s+de\s+carroll", r"naturalidade"),
        "ro": (r"data\s+na[sș]terii", r"locul\s+na[sș]terii"),
        "cs": (r"datum\s+narození", r"místo\s+narození"),
    },
    "age": {
        "en": (r"age\s*[:\-–|]?\s*\d",),
        "hu": (r"életkor",), "de": (r"alter\s*[:\-–|]\s*\d",), "fr": (r"âge",),
        "es": (r"edad",), "it": (r"età",), "nl": (r"leeftijd",), "pl": (r"wiek",),
        "pt": (r"idade",), "ro": (r"vârst[aă]",), "cs": (r"věk",),
    },
    "gender": {
        "en": (r"gender", r"sex"),
        "hu": (r"neme", r"nem"), "de": (r"geschlecht",), "fr": (r"sexe", r"genre"),
        "es": (r"sexo", r"género"), "it": (r"sesso", r"genere"), "nl": (r"geslacht",),
        "pl": (r"płeć",), "pt": (r"sexo", r"g[êé]nero"), "ro": (r"sex", r"gen"),
        "cs": (r"pohlaví",),
    },
    "marital": {
        "en": (r"marital\s+status",),
        "hu": (r"családi\s+állapot",), "de": (r"familienstand",),
        "fr": (r"(?:situation|état)\s+(?:familiale?|civil)",),
        "es": (r"estado\s+civil",), "it": (r"stato\s+civile",),
        "nl": (r"burgerlijke\s+staat",), "pl": (r"stan\s+cywilny",),
        "pt": (r"estado\s+civil",), "ro": (r"stare\s+civil[ăa]",),
        "cs": (r"rodinný\s+stav",),
    },
    "nationality": {
        "en": (r"nationality", r"citizenship"),
        "hu": (r"állampolgárság", r"nemzetiség"),
        "de": (r"staatsangehörigkeit", r"nationalität"),
        "fr": (r"nationalité",), "es": (r"nacionalidad",),
        "it": (r"nazionalità", r"cittadinanza"), "nl": (r"nationaliteit",),
        "pl": (r"obywatelstwo", r"narodowość"), "pt": (r"nacionalidade",),
        "ro": (r"cetățenie", r"naționalitate"),
        "cs": (r"státní\s+příslušnost", r"národnost"),
    },
    "religion": {
        "en": (r"religion", r"caste", r"community"),
        "hu": (r"vallás",), "de": (r"religion", r"konfession"),
        "fr": (r"religion",), "es": (r"religión",), "it": (r"religione",),
        "nl": (r"religie", r"geloof"), "pl": (r"religia", r"wyznanie"),
        "pt": (r"religião",), "ro": (r"religie",), "cs": (r"náboženství",),
    },
    "family": {
        "en": (r"father(?:'s)?\s+name", r"mother(?:'s)?\s+name",
               r"husband(?:'s)?\s+name", r"wife(?:'s)?\s+name", r"spouse",
               r"children", r"dependents"),
        "hu": (r"apja\s+neve", r"anyja\s+neve", r"házastárs", r"gyermekek"),
        "de": (r"name\s+des\s+vaters", r"name\s+der\s+mutter", r"ehepartner",
               r"kinder"),
        "fr": (r"nom\s+du\s+père", r"nom\s+de\s+la\s+mère", r"conjoint", r"enfants"),
        "es": (r"nombre\s+del\s+padre", r"nombre\s+de\s+la\s+madre", r"cónyuge",
               r"hijos"),
        "it": (r"coniuge", r"figli"), "nl": (r"echtgenoot", r"kinderen"),
        "pl": (r"małżonek", r"dzieci"), "pt": (r"cônjuge", r"filhos"),
        "ro": (r"soț(?:ie)?", r"copii"), "cs": (r"manžel(?:ka)?", r"děti"),
    },
    "health": {
        "en": (r"blood\s+group", r"health\s+status", r"disability"),
        "hu": (r"egészségi\s+állapot", r"vércsoport"),
        "de": (r"gesundheitszustand", r"behinderung", r"blutgruppe"),
        "fr": (r"état\s+de\s+santé", r"handicap"),
        "es": (r"discapacidad", r"grupo\s+sanguíneo"), "it": (r"disabilità",),
        "nl": (r"handicap",), "pl": (r"niepełnosprawność",),
        "pt": (r"deficiência",), "ro": (r"dizabilitate",), "cs": (r"zdravotní\s+stav",),
    },
    "address": {
        "en": (r"(?:permanent|current|present|residential|home|postal)\s+address",
               r"address"),
        "hu": (r"lakcím", r"lakhely", r"tartózkodási\s+hely", r"levelezési\s+cím",
               r"cím"),
        "de": (r"anschrift", r"adresse", r"wohnort", r"wohnhaft"),
        "fr": (r"adresse", r"domicile"),
        "es": (r"dirección", r"domicilio"),
        "it": (r"indirizzo", r"residenza", r"domicilio"),
        "nl": (r"adres", r"woonplaats"),
        "pl": (r"adres", r"miejsce\s+zamieszkania"),
        "pt": (r"endereço", r"morada"),
        "ro": (r"adres[aă]", r"domiciliu"),
        "cs": (r"adresa", r"bydliště"),
    },
    "contact": {
        "en": (r"contact\s*(?:no\.?|number|details|information)?", r"phone",
               r"mobile", r"tel", r"e-?mail"),
        "hu": (r"telefon(?:szám)?", r"mobil", r"e-?mail"),
        "de": (r"telefon", r"handy", r"mobil", r"tel\.", r"e-?mail"),
        "fr": (r"téléphone", r"portable", r"tél\.?", r"courriel"),
        "es": (r"teléfono", r"móvil", r"celular", r"correo\s+electrónico"),
        "it": (r"telefono", r"cellulare", r"posta\s+elettronica"),
        "nl": (r"telefoon", r"mobiel"),
        "pl": (r"telefon", r"komórka"),
        "pt": (r"telefone", r"telemóvel", r"celular"),
        "ro": (r"telefon", r"mobil"),
        "cs": (r"telefon", r"mobil"),
    },
    "id_number": {
        # India (the pilot's own set) …
        "en_in": (r"aadhaa?r\s*(?:no\.?|number)?", r"pan\s*(?:no\.?|number|card)",
                  r"passport\s*(?:no\.?|number)?"),
        # … the United States …
        "en_us": (r"ssn", r"social\s+security(?:\s+(?:no\.?|number|#))?",
                  r"driver'?s?\s+licen[cs]e\s*(?:no\.?|number|#)"),
        "hu": (r"személyi\s+(?:igazolvány|szám)", r"szig\.?\s*szám",
               r"adóazonosító(?:\s+jel)?", r"taj(?:\s*szám)?", r"útlevél(?:szám)?"),
        "de": (r"personalausweis(?:nummer)?", r"ausweisnummer", r"steuer-?id",
               r"sozialversicherungsnummer", r"reisepass"),
        "fr": (r"n(?:uméro|°)\s*(?:de\s+)?s[ée]curité\s+sociale", r"passeport",
               r"carte\s+d'identité"),
        "es": (r"dni", r"nie", r"nif", r"pasaporte", r"seguridad\s+social"),
        "it": (r"codice\s+fiscale", r"carta\s+d'identità", r"passaporto"),
        "nl": (r"bsn", r"burgerservicenummer", r"paspoort(?:nummer)?"),
        "pl": (r"pesel", r"dowód\s+osobisty", r"paszport"),
        "pt": (r"nif", r"niss", r"cartão\s+de\s+cidadão", r"passaporte"),
        "ro": (r"cnp", r"pașaport"),
        "cs": (r"rodné\s+číslo", r"číslo\s+op", r"pas"),
    },
    "photo": {
        "en": (r"photo(?:graph)?",),
        "hu": (r"fénykép", r"fotó"), "de": (r"foto", r"lichtbild"),
        "fr": (r"photo(?:graphie)?",), "es": (r"foto(?:grafía)?",),
        "it": (r"foto(?:grafia)?",), "nl": (r"foto", r"pasfoto"),
        "pl": (r"zdjęcie",), "pt": (r"foto(?:grafia)?",),
        "ro": (r"fotografie", r"poz[ăa]"), "cs": (r"fotografie",),
    },
}

# DELIBERATELY NOT LISTED: a driving-licence CLASS line ("Jogosítvány: B",
# "Führerschein Klasse B", "Driving licence: B"). That is a job qualification for
# a field-sales or service role, and masking it would destroy real evidence. The
# licence NUMBER is caught by the id_number labels and the digit-run rules.


def _labelled_line_pattern() -> re.Pattern[str]:
    """One line-level pattern from the whole table.

    The trailing separator is required for every label, which is what lets short,
    ordinary words be listed safely: "nem" is Hungarian for gender AND for "no",
    and only one of the two is followed by a colon at the start of a line."""
    forms: list[str] = []
    for by_language in _PII_LABELS.values():
        for variants in by_language.values():
            forms.extend(variants)
    # longest first so "date of birth" wins over a shorter prefix
    body = "|".join(sorted(set(forms), key=len, reverse=True))
    return re.compile(
        rf"^[\s|*•·\-]*(?:{body})\s*[:\-–|]\s*\S.*$",
        re.IGNORECASE | re.MULTILINE,
    )


_LABELED_LINE = _labelled_line_pattern()

# A bare personal-detail VALUE alone on its line (often under a removed label,
# e.g. a details table rendered as alternating label/value lines). Whole-line
# only, so these words are untouched inside a sentence.
_STANDALONE_VALUE = re.compile(
    r"^\s*(?:"
    r"male|female|married|unmarried|single|divorced|widowed|indian"
    r"|férfi|nő|házas|nőtlen|hajadon|elvált|özvegy|egyedülálló|magyar"
    r"|männlich|weiblich|divers|verheiratet|ledig|geschieden|verwitwet|deutsch"
    r"|homme|femme|masculin|féminin|marié[e]?|célibataire|divorcé[e]?|veuf|veuve|français[e]?"
    r"|hombre|mujer|masculino|femenino|casad[oa]|solter[oa]|divorciad[oa]|viud[oa]|español[a]?"
    r"|uomo|donna|maschio|femmina|coniugat[oa]|celibe|nubile|divorziat[oa]|italian[oa]"
    r"|gehuwd|ongehuwd|gescheiden|nederlands[e]?"
    r"|mężczyzna|kobieta|żonaty|zamężna|kawaler|panna|rozwiedzion[ya]|polak|polka"
    r"|casado|solteiro|divorciado|portugu[eê]s[a]?|brasileir[oa]"
    r"|bărbat|femeie|masculin|feminin|(?:ne)?căsătorit[ăa]?|divorțat[ăa]?|român[ăa]?"
    r"|muž|žena|ženatý|vdaná|svobodn[ýá]|rozveden[ýá]|čech|češka"
    r")\s*\.?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# --- Inline rules (masks inside otherwise-preserved lines) ----------------------------

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Links are masked because profile URLs usually embed the person's name
# (linkedin.com/in/amit-branaola-…). Loses GitHub links too — acceptable, since
# public_work_relevance is disabled for the pilot anyway.
_URL = re.compile(r"(?:https?://|www\.|linkedin\.com/|github\.com/)\S+", re.IGNORECASE)
_PAN_ID = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")  # Indian PAN card format (AAAAA9999A)
# A US Social Security number. Nine digits, so `_mask_number_runs` (10–13) never
# saw it; the formatted shape is unambiguous enough to mask on sight. An
# UNFORMATTED nine-digit SSN is only caught when the line carries its label.
_SSN = re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b")
# "32 years old" and its equivalents in the languages we serve.
_AGE_PHRASE = re.compile(
    r"\b\d{1,2}\s+(?:years?\s+old|éves|jahre\s+alt|ans|años|anni|jaar|lat|anos"
    r"|de\s+ani|let)\b",
    re.IGNORECASE,
)
# Postcodes only when LABELLED — a bare 6-digit number is more often money
# ("target of 750000") and masking those would destroy sales-quota evidence. The
# same reasoning gives the US ZIP its label requirement.
_PIN_CODE = re.compile(
    r"\b(?:pin\s*(?:code)?|zip\s*(?:code)?|post(?:al)?\s*code|irányítószám|plz)"
    r"\s*[:\-–]?\s*\d{4,6}(?:-\d{4})?\b",
    re.IGNORECASE,
)
# Candidate digit runs that MIGHT be phone/national-id numbers; _mask_number_runs
# confirms by digit count so year ranges (8 digits) and amounts with commas
# survive untouched.
#
# HORIZONTAL whitespace only (2026-09-25). A phone number sits on one line, and a run
# allowed to cross a line break joined a year at the end of one line to a phone at the
# start of the next: 14 digits, over the cap, so the PHONE went to the model unmasked.
# The same crossing turned a column of year ranges ("2012 - 2014⏎2014 - 2016") into
# "[PHONE]016". A leading "(" is part of the run so "(415) 555-2019" masks whole.
_NUMBER_RUN = re.compile(r"\+?\(?\d[\d \t ()\-]{7,18}\d")

# A DATE RANGE is employment evidence, not an identifier — and "06-2016 - 07-2019" is
# twelve digits joined by hyphens, which is exactly a phone number's shape, so every
# closed job on a résumé that writes dates that way reached the model as "[PHONE]"
# and only the current "…- Present" one could be counted (2026-09-25). The number pass
# runs on the text BETWEEN these, so a date is never masked and never glued to a
# neighbouring phone. Tokens: month-year, year-month or a bare year (19xx/20xx), two of
# them joined by a dash; `_plausible_range` then insists the years run forwards.
_MONTH = r"(?:0?[1-9]|1[0-2])"
_YEAR = r"(?:19|20)\d{2}"
_DATE_TOKEN = rf"(?:{_MONTH}[-/.]{_YEAR}|{_YEAR}[-/.]{_MONTH}(?!\d)|{_YEAR})"
_DATE_RANGE = re.compile(
    rf"(?<!\d){_DATE_TOKEN}[ \t ]*[-‐-―−][ \t ]*{_DATE_TOKEN}(?!\d)")
_YEAR_ONLY = re.compile(rf"^{_YEAR}$")
_RUN_GROUPS = re.compile(r"\d+")

# File-name words that are not name parts ("Rahul Sharma Resume.pdf" -> Resume).
_NAME_STOPWORDS = {
    "resume", "cv", "curriculum", "vitae", "biodata", "profile",
    "final", "updated", "latest", "new", "copy", "draft",
    # the same words on a résumé written in one of the languages we serve
    "lebenslauf", "önéletrajz", "oneletrajz", "résumé", "currículum", "curriculum",
    "levensloop", "życiorys", "zyciorys", "currículo", "curriculo", "životopis",
    "zivotopis", "hoja", "vida",
}


class RedactionResult(BaseModel):
    candidate_id: str
    redacted_text: str
    counts: dict[str, int]  # what was masked, by category — safe to log/show (no values)


def _strip_marks(text: str) -> str:
    """"Kovács" -> "Kylie". Used ONLY to build a matcher, never to rewrite the
    résumé: the text keeps its own spelling."""
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


def _name_tokens(source_file: str, text: str) -> set[str]:
    """Name parts to mask, from the two places a name reliably appears:
    the file name (recruiters save resumes by candidate name) and the resume's
    first non-empty line (the printed letterhead name).

    Unicode throughout. The ASCII version of this function is why "Kovács Péter"
    went to the model unmasked: splitting on [^A-Za-z] produced "Kov" and "ter",
    and a word-boundary search for those never matches inside the real name."""
    tokens: set[str] = set()
    for tok in re.findall(rf"{_L}+", Path(source_file).stem):
        if len(tok) >= 3 and tok.casefold() not in _NAME_STOPWORDS:
            tokens.add(tok)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        tokens |= letterhead_tokens(stripped)
        break  # only the first non-empty line — deeper lines are job content
    return tokens


def letterhead_tokens(line: str) -> set[str]:
    """Name parts from a line that looks like a printed letterhead name — 1–5
    alphabetic words, no digits; hyphens and apostrophes are part of a name
    ("Kovács-Nagy", "O'Brien") — or an empty set when it does not."""
    words = line.split()
    if not (1 <= len(words) <= 5 and all(
            re.fullmatch(rf"{_L}+(?:[-'’.]{_L}+)*\.?", w) for w in words)):
        return set()
    out = set()
    for w in words:
        w = w.strip(".'’")
        if len(w) >= 3 and w.casefold() not in _NAME_STOPWORDS:
            out.add(w)
    return out


# Combining marks (the accent half of a decomposed letter), built explicitly
# rather than written as an escape: this range is the difference between masking
# a candidate's name and not, and it should be readable by whoever edits next.
_COMBINING = "[" + chr(0x0300) + "-" + chr(0x036F) + "]"
# A letter OR a combining mark — the boundary a name must not begin inside of.
# Spelled out because a combining mark is not \w in Python, so a plain
# "not a letter" lookbehind would happily start a match mid-word after an accent.
_LETTERISH = "[^" + chr(0x0300) + "-" + chr(0x036F) + r"\W\d_]"


def _name_pattern(token: str) -> re.Pattern[str]:
    """A matcher for one name part that survives a change of spelling.

    Two things move between a file name and the printed page: accents (a
    recruiter saves "Kylie Chaaya.pdf" for a resume that prints "Kylie Chaaya"
    with them) and case. So the token is stripped to its base letters and each
    letter is allowed to carry combining marks.

    The text this runs against is DECOMPOSED first (see redact_text). That is the
    whole trick, and the first attempt at this fix missed it: composed, an
    accented letter is ONE character and no amount of "a followed by marks" will
    ever match it. Decomposed, it is the base letter plus a mark, and the pattern
    lands. Bounded by "not a letter or mark" rather than by a word boundary,
    which does not fire between a letter and a combining mark."""
    core = "".join(re.escape(ch) + _COMBINING + "*" for ch in _strip_marks(token))
    return re.compile("(?<!" + _LETTERISH + ")" + core + "(?!" + _LETTERISH + ")",
                      re.IGNORECASE)


def mask_names(text: str, tokens) -> tuple[str, int]:
    """Replace every occurrence of each name part in `tokens` with [CANDIDATE] —
    accent- and case-insensitively, on a DECOMPOSED copy (see _name_pattern) — and
    collapse "[CANDIDATE] [CANDIDATE]" into one. Returns (text, hits). redact_text's
    name step, and the Bulk Print fallback's, which masks names it read from a
    table of contents rather than from a file name."""
    decomposed = unicodedata.normalize("NFD", text)
    hits = 0
    for token in tokens:
        decomposed, n = _name_pattern(token).subn("[CANDIDATE]", decomposed)
        hits += n
    out = unicodedata.normalize("NFC", decomposed)
    # "AMIT BRANAOLA" -> "[CANDIDATE] [CANDIDATE]" reads badly; collapse to one.
    out = re.sub(r"\[CANDIDATE\](?:[\s,.]*\[CANDIDATE\])+", "[CANDIDATE]", out)
    return out, hits


def _mask_number_runs(text: str) -> tuple[str, int]:
    """Mask digit runs that are identifiers rather than job content.

    10–13 digits always (phones the world over, Aadhaar). NINE digits only when
    the run is written like a number and not like a quantity — it carries a
    separator or a leading + / 0 — which covers Spanish, Polish, Czech and
    Portuguese national mobiles ("612 345 678") while leaving a bare nine-digit
    integer in a sales figure alone. Deliberately NOT masking shorter runs: years
    (4), year ranges (8), big money amounts (comma-separated, so each run stays
    short).

    Longer than 13 digits (2026-09-25): these used to be left alone, and that is how
    a phone number glued to a year beside it ("since 2019 98765 43210") went through
    whole. Now privacy wins — the run is masked unless every group in it is a year,
    which is a list of years and never a phone. Date ranges are cut out before any
    of this (see _DATE_RANGE)."""
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        raw = match.group()
        digits = sum(ch.isdigit() for ch in raw)
        formatted = bool(re.search(r"[\s()\-]", raw)) or raw[0] in "+0("
        if 10 <= digits <= 13 or (digits == 9 and formatted):
            count += 1
            return "[PHONE]"
        if digits > 13 and not all(_YEAR_ONLY.match(g) for g in _RUN_GROUPS.findall(raw)):
            count += 1
            return "[PHONE]"
        return raw

    out: list[str] = []
    pos = 0
    for m in _DATE_RANGE.finditer(text):
        if not _plausible_range(m.group()):
            continue
        out.append(_NUMBER_RUN.sub(repl, text[pos:m.start()]))
        out.append(m.group())
        pos = m.end()
    out.append(_NUMBER_RUN.sub(repl, text[pos:]))
    return "".join(out), count


def _plausible_range(raw: str) -> bool:
    """Two years, running forwards, within a working life. What keeps a digit group
    that merely LOOKS like a year pair from being waved through as a date."""
    years = [int(y) for y in re.findall(_YEAR, raw)]
    return len(years) == 2 and years[0] <= years[1] <= years[0] + 60


def redact_text(text: str, candidate_id: str, source_file: str, *,
                name_tokens=None) -> RedactionResult:
    """Apply all rules in a fixed order. Order matters:
    1. whole-line removals first (their values disappear with the line),
    2. inline masks on what's left,
    3. name tokens last (they can appear anywhere, including inside kept lines),
    4. cosmetic cleanup (collapse the blank runs removals leave behind).

    `name_tokens` replaces the name parts read from the file name and the first line
    — for a caller that knows the names better (the Bulk Print fallback reads them
    from a table of contents). None = the résumé's own, as always."""
    counts: dict[str, int] = {}

    def sub(category: str, pattern: re.Pattern[str], replacement: str, s: str) -> str:
        out, n = pattern.subn(replacement, s)
        if n:
            counts[category] = counts.get(category, 0) + n
        return out

    # One normal form before anything is matched, so a précomposed "é" and a
    # decomposed "e + ́" are the same character to every rule below.
    s = unicodedata.normalize("NFC", text)
    s = sub("labeled_lines", _LABELED_LINE, REMOVED, s)
    s = sub("standalone_values", _STANDALONE_VALUE, REMOVED, s)
    s = sub("emails", _EMAIL, "[EMAIL]", s)
    s = sub("links", _URL, "[LINK]", s)
    s = sub("id_numbers", _PAN_ID, "[ID]", s)
    s = sub("id_numbers", _SSN, "[ID]", s)
    s = sub("age_phrases", _AGE_PHRASE, "[AGE]", s)
    s = sub("pin_codes", _PIN_CODE, "[PIN]", s)
    s, n_phones = _mask_number_runs(s)
    if n_phones:
        counts["phone_numbers"] = n_phones

    # Names are masked on a DECOMPOSED copy so an accent is a separate character
    # the matcher can skip, then what survives is recomposed — the resume keeps
    # its own spelling, and a plain-ASCII filename still reaches an accented name.
    if name_tokens is None:
        name_tokens = _name_tokens(source_file, unicodedata.normalize("NFC", text))
    s, name_hits = mask_names(s, name_tokens)
    if name_hits:
        counts["name_tokens"] = name_hits

    # Cosmetic: collapse stacked removal markers and 3+ blank lines.
    s = re.sub(rf"(?:{re.escape(REMOVED)}\s*\n)+", REMOVED + "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()

    return RedactionResult(candidate_id=candidate_id, redacted_text=s, counts=counts)


def record_candidate(candidate_id: str, source_file: str, out_dir: str | Path = "output") -> Path:
    """Maintain the PRIVATE candidate_id -> source_file map (§6.2).

    This is the only sanctioned bridge back from an anonymous id to a real file/
    person, used by the UI at decision time. It lives under output/ (gitignored)
    and must never be shipped, logged, or fed to a model."""
    from resume_screener.locking import file_lock

    path = Path(out_dir) / "private" / "candidate_map.json"
    # Cross-process lock + atomic replace (P26): concurrent ingest workers (and the
    # API process) read-modify-write this map; a torn write would orphan every id.
    with file_lock(path):
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data[candidate_id] = str(source_file)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    return path
