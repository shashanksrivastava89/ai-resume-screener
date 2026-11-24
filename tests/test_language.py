"""Which language is this document in? (global rollout, 2026-09-12). Offline.

The old answer was "English unless it is written in a non-Latin script", which
made every European language English. That only ever put a badge on a card, but
the badge is how a recruiter notices they are about to screen against a JD they
cannot read — and it said nothing for the very pilot it is now needed for.
"""

from __future__ import annotations

from resume_screener import language
from resume_screener.api.service import jd_language

HU = """A Acme Magyarország Kft. minőségbiztosítási szakembert keres a
budapesti irodájába. A jelölt feladata a minőségirányítási rendszer
működtetése és fejlesztése, valamint a belső auditok lebonyolítása.
Elvárás a felsőfokú végzettség és legalább három év tapasztalat, illetve
az angol nyelv magabiztos ismerete. Tiare kínálunk: versenyképes fizetés,
rugalmas munkavégzés és szakmai fejlődési lehetőség."""

DE = """Für unseren Standort in München suchen wir eine Leiterin Rechnungswesen.
Sie verantworten den Monatsabschluss nach HGB und IFRS, führen ein Team von
fünf Mitarbeitenden und arbeiten eng mit der Konzernzentrale zusammen. Wir
erwarten ein abgeschlossenes Studium und mehrjährige Erfahrung in einer
vergleichbaren Position sowie sehr gute Kenntnisse in SAP."""

EN = """We are looking for a Quality Systems Specialist to join the team in
Budapest. You will maintain and improve the quality management system, run
internal audits and work with the manufacturing teams on corrective actions.
The role requires a degree in a scientific discipline and at least three years
of experience in a regulated environment, along with fluent English."""

HI = """हम बजट और पूर्वानुमान के लिए एक वरिष्ठ वित्त प्रबंधक की तलाश कर रहे हैं।
इस भूमिका में मासिक समापन, वैधानिक लेखा परीक्षा और कर अनुपालन की जिम्मेदारी है।
उम्मीदवार के पास कम से कम पांच वर्ष का अनुभव होना चाहिए।"""


def test_a_hungarian_job_description_is_hungarian():
    assert language.detect(HU) == "hu"
    assert jd_language(HU) == "hu"


def test_a_german_job_description_is_german():
    assert language.detect(DE) == "de"


def test_an_english_job_description_is_english():
    assert language.detect(EN) == "en"


def test_a_non_latin_script_is_still_reported_as_such():
    """The original heuristic, kept for the case it was actually built for."""
    assert language.detect(HI) == "non_english"


def test_a_document_too_short_to_judge_is_not_guessed_at():
    assert language.detect("Accountant wanted.") is None
    assert language.detect("") is None


def test_the_code_becomes_a_name_a_recruiter_reads():
    assert language.name_of("hu") == "Hungarian"
    assert language.name_of("non_english") == "non-English"
    assert language.name_of(None) == ""
    assert language.name_of("xx") == "xx"
