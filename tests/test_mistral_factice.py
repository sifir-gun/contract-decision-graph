"""Serveur factice de l'API de Mistral (PR C3) : le vrai SDK (mistralai) et les vrais
adaptateurs du projet, pointés sur lui par l'adresse de l'API (`server_url`), obtiennent des
réponses valides pour chaque schéma demandé. Il sert aux tests du cluster, dans une image de
test à part ; jamais dans l'image de l'application (tests/test_image.py)."""

import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from mistral_factice import serve

from cdg.adapters.llm.mistral import MistralProvider
from cdg.application import demo_set
from cdg.application.crag import GradeOutput, RewriteOutput
from cdg.application.explanation import LLMExplainer
from cdg.application.extraction import LLMExtractor
from cdg.domain import masking
from cdg.domain.config import load_config
from cdg.domain.explanation import ExplanationRequest, FindingToExplain, refusals

CONFIG = load_config()
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def factice():
    server = serve("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def provider(url: str) -> MistralProvider:
    return MistralProvider(CONFIG.llm, api_key="cle-factice", server_url=url)


def control(url: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"{url}/controle", data=data, method="GET")
    if data is not None:
        request.method = "POST"
        request.add_header("content-type", "application/json")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def test_extraction_attendue_pour_chaque_contrat_du_jeu(factice):
    """Les clauses d'une extraction correcte (data/contracts/attendus.yaml), par le vrai
    SDK et le vrai extracteur, comme l'extraction simulée de la démonstration."""
    _, contracts = demo_set.load()
    extractor = LLMExtractor(provider(factice))
    analysed = [c for c in contracts.values() if c.clauses]
    assert len(analysed) >= 10
    for contract in analysed:
        masked = masking.mask(contract.text(), contract.parties).text
        result = extractor(masked, [])
        assert result.clauses == list(contract.clauses), contract.id
        assert result.usage[0].tokens_in > 0


def test_texte_hors_du_jeu_refuse_explicitement(factice):
    with pytest.raises(Exception) as raised:
        LLMExtractor(provider(factice))("Contrat inconnu du jeu.", [])
    assert "422" in str(raised.value) or "hors du jeu" in str(raised.value)
    assert control(factice)["refus"] == 1


def test_juge_du_crag_retient_tous_les_extraits(factice):
    blocks = "\n\n".join(
        f"<<<EXTRAIT {n}>>>\nréf. {n}\ntexte {n}\n<<<FIN EXTRAIT {n}>>>"
        for n in (1, 2, 3)
    )
    output, usage = provider(factice).structured(
        tier="light",
        system="juge",
        user=f"Domaine : x\nClause : y\nRecherche : z\n\n{blocks}",
        schema=GradeOutput,
        node="crag_grade:test",
    )
    assert output.relevant == [1, 2, 3]
    assert usage.node == "crag_grade:test"


def test_reecriture_rend_la_derniere_requete(factice):
    user = "Domaine : x\nClause : y\nRequêtes déjà essayées :\n- première\n- seconde"
    output, _ = provider(factice).structured(
        tier="light", system="réécriture", user=user, schema=RewriteOutput, node="r"
    )
    assert output.query == "seconde"


def test_explication_acceptee_par_les_controles_du_domaine(factice):
    request = ExplanationRequest(
        decision="GO_RESERVES",
        proposed_decision="GO_RESERVES",
        margin=0.1,
        human=None,
        failure_stage=None,
        findings=[
            FindingToExplain(
                id="juridique-1",
                domain="juridique",
                kind="responsabilite_fournisseur",
                text="Plafond de responsabilité inférieur au seuil : NO GO évité.",
                references=["Code civil, article 1231-5"],
            ),
            FindingToExplain(
                id="financier-1",
                domain="financier",
                kind=None,
                text="Aucune référence retenue.",
                references=[],
            ),
        ],
    )
    draft, _ = LLMExplainer(provider(factice))(request, [])
    assert refusals(draft, request) == []


def test_delai_reglable_et_appels_comptes(factice):
    assert control(factice, {"delai": 0.4}) == {"delai": 0.4}
    start = time.monotonic()
    provider(factice).structured(
        tier="light",
        system="s",
        user="Requêtes déjà essayées :\n- q",
        schema=RewriteOutput,
        node="r",
    )
    assert time.monotonic() - start >= 0.4
    counts = control(factice)
    assert counts["appels"] == {"RewriteOutput": 1}
    assert counts["recues"] == 1  # comptée à l'arrivée, avant le délai
    assert counts["delai"] == 0.4


def test_requete_comptee_des_son_arrivee(factice):
    """Une analyse en cours se voit avant la réponse : le scénario d'arrêt coupe le pod à
    ce moment-là."""
    control(factice, {"delai": 1.0})
    worker = threading.Thread(
        target=provider(factice).structured,
        kwargs={
            "tier": "light",
            "system": "s",
            "user": "Requêtes déjà essayées :\n- q",
            "schema": RewriteOutput,
            "node": "r",
        },
    )
    worker.start()
    time.sleep(0.3)
    counts = control(factice)
    assert counts["recues"] == 1 and counts["appels"] == {}
    worker.join(5)


# --- image de test à part ----------------------------------------------------------------

FACTICE = ROOT / "docker" / "mistral-factice"


def test_image_de_test_part_de_l_application_et_n_ajoute_que_le_serveur():
    lines = [
        line.strip()
        for line in (FACTICE / "Dockerfile").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert lines[:2] == ["ARG APPLICATION=cdg:verification", "FROM ${APPLICATION}"]
    copies = [line for line in lines if line.startswith(("COPY", "ADD"))]
    assert copies == ["COPY tests/mistral_factice.py /test/mistral_factice.py"]
    ignored = (FACTICE / "Dockerfile.dockerignore").read_text(encoding="utf-8")
    admitted = [
        line for line in ignored.splitlines() if line and not line.startswith("#")
    ]
    assert admitted == ["*", "!tests/mistral_factice.py"]
