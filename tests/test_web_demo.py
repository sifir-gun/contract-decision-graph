"""Mode démonstration de l'interface (décision du 27/09, point 17) : sans clé d'API, sans
coût, sans PostgreSQL. Extraction simulée à partir des attendus, références choisies par
rattachement déclaré, explication par le gabarit ; règles, décision, détection des
instructions, revue humaine, scellement et vérification tournent pour de vrai, en mémoire.
"""

import json
from datetime import date

import demo_set as expected_set
import pytest
from web_helpers import client, csrf

from cdg import cli
from cdg.adapters.demo.extraction import DemoExtractionError, ExpectedExtractor
from cdg.adapters.demo.references import DeclaredCrag
from cdg.adapters.web import server
from cdg.application import demo_set, ingestion
from cdg.domain import masking
from cdg.domain.config import load_config
from cdg.domain.models import Clause

CONFIG = load_config()
BANNER = (
    "Démonstration : extraction simulée à partir des résultats attendus, aucun appel à "
    "un LLM, rien n'est scellé dans le vrai journal"
)
_, EXPECTED = expected_set.load()
EXPECTED = {c.id: c for c in EXPECTED}


def forbidden(*args, **kwargs):
    raise AssertionError("le mode démonstration n'appelle ni LLM ni PostgreSQL")


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setattr(cli, "build_provider", forbidden)
    monkeypatch.setattr(cli.conninfo, "app_conninfo", forbidden)
    monkeypatch.setattr(cli.conninfo, "admin_conninfo", forbidden)
    service = cli.demo_service(CONFIG)
    return client(service, demo=True), service


def analyse_demo(web, contract_id: str) -> str:
    token = csrf(web)
    response = web.post(
        "/analyse",
        data={
            "csrf": token,
            "source": "jeu",
            "contrat": contract_id,
            "identifiant": contract_id,
        },
    )
    assert response.status_code == 303, response.text
    return contract_id


@pytest.mark.parametrize("page", ["/", "/analyse", "/journal", "/administration"])
def test_bandeau_permanent(demo, page):
    web, _ = demo
    assert BANNER in web.get(page).text


def test_bandeau_absent_du_mode_reel():
    assert BANNER not in client().get("/").text


def test_formulaire_limite_aux_contrats_du_jeu(demo):
    web, _ = demo
    page = web.get("/analyse").text
    assert 'id="texte"' not in page and 'id="fichier"' not in page
    assert 'id="parties"' not in page  # parties déclarées du jeu, masquées d'office
    token = csrf(web)
    response = web.post(
        "/analyse",
        data={"csrf": token, "source": "texte", "texte": "Contrat collé."},
    )
    assert response.status_code == 400
    assert "contrats du jeu" in response.text


@pytest.mark.parametrize("contract_id", sorted(EXPECTED))
def test_chaque_contrat_du_jeu_rend_l_issue_attendue(demo, contract_id):
    web, service = demo
    contract = EXPECTED[contract_id]
    analyse_demo(web, contract_id)
    status = service.dossier(contract_id)["status"]
    wanted = contract.expected
    if contract.rejected:
        assert status["reject_reason"]
        return
    assert status["proposed_decision"] == wanted["proposed_decision"]
    if contract.human is None:
        assert (status["statut"], status["final_decision"]) == (
            "termine",
            wanted["final_decision"],
        )
        return
    assert status["statut"] == "suspendu"
    token = csrf(web)
    response = web.post(
        f"/contrats/{contract_id}/decision",
        data={
            "csrf": token,
            "decision": contract.human["decision"],
            "relecteur": contract.human["reviewer"],
            "motif": contract.human["reason"],
        },
    )
    assert response.status_code == 303
    final = service.dossier(contract_id)["status"]
    assert final["final_decision"] == wanted["final_decision"]


def test_parties_du_jeu_masquees_d_office(demo):
    web, service = demo
    analyse_demo(web, "demo-06-no-go-conseil")
    raw = service.engine.values("demo-06-no-go-conseil")["raw_text"]
    for party in EXPECTED["demo-06-no-go-conseil"].parties:
        assert party not in raw


def test_journal_en_memoire_verifie_et_rejoue(demo):
    web, service = demo
    analyse_demo(web, "demo-01-go-maintenance")
    analyse_demo(web, "demo-06-no-go-conseil")
    assert [e["thread_id"] for e in service.journal()] == [
        "demo-01-go-maintenance",
        "demo-06-no-go-conseil",
    ]
    check = web.get("/journal/verification", headers={"hx-request": "true"})
    assert "Chaîne intègre" in check.text and "2 enregistrements" in check.text
    replay = web.get(
        "/contrats/demo-06-no-go-conseil/rejeu", headers={"hx-request": "true"}
    )
    assert "Rejeu identique" in replay.text


def test_consommation_simulee_et_explication_par_le_gabarit(demo):
    web, service = demo
    analyse_demo(web, "demo-06-no-go-conseil")
    dossier = service.dossier("demo-06-no-go-conseil")
    assert {m for u in dossier["consommation"] for m in u["models"]} == {"simulation"}
    assert dossier["status"]["explanation"]["source"] == "gabarit"
    assert "démonstration" in dossier["status"]["explanation"]["reasons"][0]


# --- adaptateurs de démonstration ----------------------------------------------------------


def test_extraction_simulee_des_seuls_contrats_du_jeu():
    _, contracts = demo_set.load()
    contract = contracts["demo-06-no-go-conseil"]
    extractor = ExpectedExtractor(contracts.values())
    masked = masking.mask(contract.text(), contract.parties).text
    result = extractor(masked, [])
    assert result.clauses == list(contract.clauses)
    assert [u.model for u in result.usage] == ["simulation"]
    with pytest.raises(DemoExtractionError, match="contrats du jeu"):
        extractor("Un contrat qui n'est pas dans le jeu.", [])


def test_references_par_rattachement_declare():
    crag = DeclaredCrag(CONFIG.corpus.chunk_max_words)
    clause = Clause(kind="delai_paiement", present=True, quote="x", value=90)
    result = crag("financier", [clause], date(2026, 9, 25))
    [trace] = result.trace.clauses
    declared = {
        meta["reference"]
        for meta, _, kinds in ingestion.pending_chunks(CONFIG.corpus.chunk_max_words)
        if "delai_paiement" in kinds
    }
    assert trace.retained and set(trace.retained) <= declared
    assert "C. com., art. L441-10" in trace.retained
    assert result.usage == []  # ni recherche vectorielle ni juge LLM


def test_reference_expiree_signalee_jamais_retenue():
    crag = DeclaredCrag(CONFIG.corpus.chunk_max_words)
    clause = Clause(kind="delai_paiement", present=True, quote="x", value=90)
    result = crag("financier", [clause], date(2027, 3, 1))
    [trace] = result.trace.clauses
    assert "C. com., art. L441-10" in trace.expired
    assert "C. com., art. L441-10" not in trace.retained
    assert any("expirée" in f for f in result.trace.findings)


def test_commande_web_demo(monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_provider", forbidden)
    monkeypatch.setattr(cli.conninfo, "app_conninfo", forbidden)
    seen = {}
    monkeypatch.setattr(
        server, "serve", lambda app, host, port, *, log_config: seen.update(app=app)
    )
    assert cli.main(["web", "--demo"]) == 0
    assert json.loads(capsys.readouterr().out) == {"web": "arrêtée"}
    from fastapi.testclient import TestClient

    page = TestClient(seen["app"], base_url="http://127.0.0.1:8000").get("/")
    assert BANNER in page.text


def test_date_d_analyse_par_defaut_celle_des_attendus(demo):
    web, service = demo
    analyse_demo(web, "demo-01-go-maintenance")
    expected_on, _ = demo_set.load()
    status = service.dossier("demo-01-go-maintenance")["status"]
    assert status["analysis_date"] == expected_on == date(2026, 9, 25)
    assert "25 septembre 2026" in web.get("/analyse").text
