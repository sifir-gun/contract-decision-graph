"""Échéances du corpus (décision du 27/09) : l'audit hebdomadaire de la CI échoue si une
source du corpus cesse d'être valide dans les 60 jours, avec la source et la date.

Première échéance du corpus : l'article L441-10 du Code de commerce, et la fiche qui le
paraphrase, cessent d'être valides le 01/01/2027 ; le contrôle échoue donc à partir du
02/11/2026."""

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from cdg.application import ingestion
from cdg.domain.corpus import expiring

ROOT = Path(__file__).resolve().parents[1]
COMMAND = "uv run --no-sync python scripts/echeances_corpus.py --jours 60"
L441_10 = "C. com., art. L441-10"
FICHE = "Fiche projet : Délais de paiement entre professionnels"
END = date(2027, 1, 1)


def script():
    path = ROOT / "scripts" / "echeances_corpus.py"
    spec = importlib.util.spec_from_file_location("echeances_corpus", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- règle du domaine -----------------------------------------------------------------------


def test_echeance_dans_l_horizon_bornes_comprises():
    validities = {
        "a": date(2026, 11, 26),  # dernier jour de l'horizon
        "b": date(2026, 11, 27),  # le lendemain : hors horizon
        "c": date(2026, 9, 1),  # déjà expirée
        "d": None,  # version sans fin de validité
    }
    assert expiring(validities, date(2026, 9, 27), 60) == [
        ("c", date(2026, 9, 1)),
        ("a", date(2026, 11, 26)),
    ]


def test_echeances_triees_par_date_puis_par_source():
    same = date(2027, 1, 1)
    assert expiring({"z": same, "a": same}, date(2026, 12, 1), 60) == [
        ("a", same),
        ("z", same),
    ]


# --- corpus du dépôt ------------------------------------------------------------------------


def test_fins_de_validite_des_sources_du_corpus():
    validities = ingestion.source_validities()
    # une fiche expire avec les articles qu'elle paraphrase
    assert validities[L441_10] == END and validities[FICHE] == END
    assert {ref for ref, end in validities.items() if end is not None} == {
        L441_10,
        FICHE,
    }
    assert len(validities) == 22 + 7  # articles du manifeste et fiches


# --- script lancé par la CI -------------------------------------------------------------------


@pytest.mark.parametrize("on", ["2026-09-27", "2026-11-01"])
def test_script_reussit_hors_horizon(on, capsys):
    assert script().main(["--jours", "60", "--date", on]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"date": on, "horizon_jours": 60, "echeances": []}


def test_script_echoue_avec_la_source_et_la_date(capsys):
    assert script().main(["--jours", "60", "--date", "2026-11-02"]) == 1
    err = json.loads(capsys.readouterr().err)
    assert err["erreur"] == "sources du corpus qui expirent dans les 60 jours"
    assert err["echeances"] == [
        {"source": L441_10, "fin_de_validite": "2027-01-01"},
        {"source": FICHE, "fin_de_validite": "2027-01-01"},
    ]


@pytest.mark.parametrize("days", ["0", "-5", "soixante"])
def test_script_horizon_invalide_refuse(days):
    with pytest.raises(SystemExit) as exc:
        script().main(["--jours", days])
    assert exc.value.code == 2


def test_audit_hebdomadaire_de_la_ci_lance_le_controle():
    """Le job `audit`, seul lancé par le déclenchement planifié du lundi, fait le
    contrôle ; son nom reste celui qu'exige la règle de protection de `main`."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    audit = workflow["jobs"]["audit"]
    assert audit["name"] == "audit (pip-audit)" and "if" not in audit
    runs = [" ".join(step["run"].split()) for step in audit["steps"] if "run" in step]
    assert COMMAND in runs
    assert COMMAND in (ROOT / "scripts" / "check.sh").read_text()
