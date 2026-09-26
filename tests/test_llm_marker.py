"""Tests `llm` : exclus par défaut, jamais sautés en silence, activés par --llm."""

from pathlib import Path

pytest_plugins = ["pytester"]

CONFTEST = Path(__file__).with_name("conftest.py").read_text(encoding="utf-8")
SAMPLE = """
import pytest

@pytest.mark.llm
def test_vrai_modele():
    pass

def test_logique():
    pass
"""


def test_tests_llm_exclus_par_defaut_et_comptes(pytester):
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(SAMPLE)
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, deselected=1)
    result.stdout.fnmatch_lines(["*tests llm : exclus*--llm*"])


def test_tests_llm_actives_par_option(pytester):
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(SAMPLE)
    result = pytester.runpytest("-p", "no:cacheprovider", "--llm")
    result.assert_outcomes(passed=2)


def test_exclure_pg_n_active_pas_les_tests_llm(pytester):
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(SAMPLE)
    result = pytester.runpytest("-p", "no:cacheprovider", "-m", "not pg")
    result.assert_outcomes(passed=1, deselected=1)
