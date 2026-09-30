"""Accès d'urgence et canaux dans le cluster (PR D2, ADR 005).

- Dans le cluster (variable KUBERNETES_SERVICE_HOST, posée par le kubelet), une décision
  par la CLI (resume, expire) n'est admise qu'avec --urgence MOTIF : événement
  acces_urgence au journal des accès, canal cli et urgence scellés. Sans elle, refus
  clair : c'est ce qui ferme le contournement des quatre yeux par changement de porte.
- L'interface locale, non authentifiée, ne démarre pas dans le cluster.
- L'opérateur est un identifiant non nominatif ; le motif d'urgence est borné.
"""

import dataclasses
import json
from datetime import UTC, datetime, timedelta

import pytest
from doubles import ACTEUR_ANALYSTE
from web_helpers import PENDING_TEXT, memory_service

from cdg import cli

RESUME = ["resume", "c-attente", "--decision", "NO_GO", "--reason", "revu"]


def run_cli(capsys, *argv) -> tuple[int, dict]:
    """Résultat de la commande. Les journaux vont aussi sur la sortie standard (collectés
    dans Kubernetes) : l'événement acces_urgence précède le résultat JSON."""
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    text = out if code == 0 else err
    start = 0 if text.startswith("{") else text.index("\n{") + 1
    return code, json.loads(text[start:])


@pytest.fixture
def pending(monkeypatch):
    """Contrat analysé dans l'interface authentifiée, en attente de revue."""
    service = memory_service()
    service.analyse(PENDING_TEXT, contract_id="c-attente", actor=ACTEUR_ANALYSTE)
    monkeypatch.setattr(cli, "build_service", lambda config: service)
    return service


@pytest.fixture
def cluster(monkeypatch):
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.43.0.1")


def test_contournement_par_la_cli_refuse_dans_le_cluster(pending, cluster, capsys):
    code, error = run_cli(capsys, *RESUME, "--operateur", "astreinte-1")
    assert code == 1 and error["erreur"] == "AccesRefuse"
    assert "--urgence" in error["detail"]
    assert pending.dossier("c-attente")["etat"] == "en_attente"  # rien tranché


def test_meme_operation_acceptee_et_tracee_en_urgence(pending, cluster, capsys):
    motif = "fournisseur d'identité injoignable, incident 42"
    argv = ["--journaux", "json", *RESUME, "--operateur", "astreinte-1"]
    code = cli.main([*argv, "--urgence", motif])
    out = capsys.readouterr().out
    first, _, rest = out.partition("\n")
    assert code == 0
    event = json.loads(first)  # journal des accès, en JSON sur la sortie standard
    assert (event["journal"], event["message"]) == ("cdg.acces", "acces_urgence")
    assert (event["operateur"], event["commande"], event["motif"]) == (
        "astreinte-1",
        "resume",
        motif,
    )
    status = json.loads(rest[rest.index("{") :])
    assert status["final_decision"] == "NO_GO"
    assert status["human"]["acteur"] == {
        "canal": "cli",
        "authentifie": False,
        "iss": None,
        "sub": None,
        "operateur": "astreinte-1",
        "urgence": True,
    }


def test_expiration_par_la_cli_en_urgence_seulement_dans_le_cluster(
    pending, cluster, capsys, monkeypatch
):
    later = dataclasses.replace(
        pending, now=lambda: datetime.now(UTC) + timedelta(days=2)
    )
    monkeypatch.setattr(cli, "build_service", lambda config: later)
    expire = ["expire", "--older-than", "1d", "--operateur", "astreinte-1"]
    code, error = run_cli(capsys, *expire)
    assert code == 1 and "--urgence" in error["detail"]
    code, out = run_cli(capsys, *expire, "--urgence", "contrats bloqués, incident 43")
    assert code == 0
    [expired] = out["expired"]
    assert expired["human"]["acteur"]["urgence"] is True
    assert expired["human"]["source"] == "systeme"


def test_hors_du_cluster_la_cli_decide_sans_urgence(pending, capsys, monkeypatch):
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    code, status = run_cli(capsys, *RESUME, "--operateur", "relecteur-poste")
    assert code == 0 and status["human"]["acteur"]["urgence"] is False


@pytest.mark.parametrize("operator", ["Camille Martin", "camille@example.org", "R"])
def test_operateur_nominatif_refuse(pending, capsys, operator):
    code, error = run_cli(capsys, *RESUME, "--operateur", operator)
    assert code == 1 and "non nominatif" in error["detail"]


@pytest.mark.parametrize("motif", ["   ", "x" * 201])
def test_motif_d_urgence_vide_ou_trop_long_refuse(pending, cluster, capsys, motif):
    code, error = run_cli(
        capsys, *RESUME, "--operateur", "astreinte-1", "--urgence", motif
    )
    assert code == 1 and "200 caractères" in error["detail"]


def test_interface_locale_refusee_dans_le_cluster(cluster, capsys):
    code, error = run_cli(capsys, "web")
    assert code == 1 and "--identite en-tetes" in error["detail"]
