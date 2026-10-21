"""Serveur factice de l'API de Mistral (PR C3) : le vrai SDK (mistralai) et les vrais
adaptateurs du projet, pointés sur lui par l'adresse de l'API (`server_url`), obtiennent des
réponses valides pour chaque schéma demandé. Il sert aux tests du cluster, dans une image de
test à part ; jamais dans l'image de l'application (tests/test_image.py)."""

import json
import threading
import time
import urllib.request
from pathlib import Path

import mistral_factice
import pytest
from mistral_factice import serve
from mistralai.extra.utils.response_format import response_format_from_pydantic_model

from cdg.adapters.llm.mistral import MistralProvider
from cdg.application import demo_set, extraction, ingestion
from cdg.application.crag import GradeOutput, RewriteOutput
from cdg.application.explanation import LLMExplainer
from cdg.application.extraction import LLMExtractor
from cdg.domain import masking, profiles
from cdg.domain.config import load_config
from cdg.domain.explanation import (
    Draft,
    ExplanationRequest,
    FindingToExplain,
    refusals,
)

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
    SDK et le vrai extracteur, comme l'extraction simulée de la démonstration : les
    contrats des deux profils, chacun sous le schéma de son profil."""
    _, contracts = demo_set.load()
    extractor = LLMExtractor(provider(factice))
    analysed = [c for c in contracts.values() if c.clauses]
    # le seul contrat sans clause attendue est rejeté à l'entrée, avant toute extraction
    assert set(contracts) - {c.id for c in analysed} == {"demo-10-rejet-anglais"}
    assert {c.profile for c in analysed} == set(profiles.PROFILES)
    for contract in analysed:
        masked = masking.mask(contract.text(), contract.parties).text
        result = extractor(masked, [], contract.profile)
        assert result.clauses == list(contract.clauses), contract.id
        assert result.usage[0].tokens_in > 0


def test_schema_de_chaque_profil_reconnu():
    """Le serveur lit `extraction.SCHEMAS` : un schéma de profil ajouté y est reconnu sans
    liste à tenir. Le nom que le SDK met dans `response_format.json_schema.name` est le
    `__name__` du modèle (mistralai 2.10.1, extra/utils/response_format.py)."""
    assert mistral_factice.PROFILE_OF_SCHEMA == {
        schema.__name__: profile for profile, schema in extraction.SCHEMAS.items()
    }
    assert set(mistral_factice.PROFILE_OF_SCHEMA.values()) == set(profiles.PROFILES)
    state = mistral_factice.State()
    _, contracts = demo_set.load()
    for profile, schema in extraction.SCHEMAS.items():
        sent = response_format_from_pydantic_model(schema)
        assert sent["json_schema"]["name"] == schema.__name__
        contract = next(c for c in contracts.values() if c.profile == profile)
        masked = masking.mask(contract.text(), contract.parties).text
        message = f"<<<CONTRAT-ab12>>>\n{masked}\n<<<FIN-CONTRAT-ab12>>>"
        answered = mistral_factice.answer(state, sent["json_schema"]["name"], message)
        assert [c["kind"] for c in answered["clauses"]] == [
            c.kind for c in contract.clauses
        ], profile


def test_noms_des_schemas_distincts():
    """Le serveur choisit sa réponse d'après ce nom seul : deux schémas de même nom, ou
    un nom partagé avec ceux de l'explication et du CRAG, le laisseraient sans savoir à
    quoi répondre."""
    names = [schema.__name__ for schema in extraction.SCHEMAS.values()]
    assert len(names) == len(set(names)) == len(profiles.PROFILES)
    assert not set(names) & {
        GradeOutput.__name__,
        RewriteOutput.__name__,
        Draft.__name__,
    }
    assert set(mistral_factice.PROFILE_OF_SCHEMA) == set(names)


@pytest.mark.parametrize("profile", profiles.PROFILES)
def test_texte_hors_du_jeu_refuse_explicitement(factice, profile):
    with pytest.raises(Exception) as raised:
        LLMExtractor(provider(factice))("Contrat inconnu du jeu.", [], profile)
    assert "422" in str(raised.value) or "hors du jeu" in str(raised.value)
    assert control(factice)["refus"] == 1


def other_profile(profile: str) -> str:
    return next(p for p in profiles.PROFILES if p != profile)


CROSSED = [
    ("demo-01-go-maintenance", "fournisseur"),
    ("st-01-go-cabine", "sous_traitance"),
]


@pytest.mark.parametrize(("contract_id", "declared"), CROSSED)
def test_contrat_du_jeu_sous_le_schema_d_un_autre_profil_refuse(
    factice, contract_id, declared
):
    """Un contrat du jeu reçu sous le schéma d'un autre profil est refusé (422) et nomme
    son profil : jamais les clauses d'un autre genre de contrat, ni « hors du jeu »."""
    _, contracts = demo_set.load()
    contract = contracts[contract_id]
    assert contract.profile == declared
    asked = other_profile(declared)
    masked = masking.mask(contract.text(), contract.parties).text
    message = f"<<<CONTRAT-ab12>>>\n{masked}\n<<<FIN-CONTRAT-ab12>>>"
    with pytest.raises(mistral_factice.Refused) as direct:
        mistral_factice.answer(
            mistral_factice.State(), extraction.SCHEMAS[asked].__name__, message
        )
    assert f"contrat du jeu du profil {declared}" in str(direct.value)
    assert "hors du jeu" not in str(direct.value)
    # par le SDK : l'extracteur du profil demandé, pointé sur le serveur
    with pytest.raises(Exception) as raised:
        LLMExtractor(provider(factice))(masked, [], asked)
    assert raised.value.status_code == 422
    assert f"contrat du jeu du profil {declared}" in str(raised.value)
    assert "hors du jeu" not in str(raised.value)
    assert control(factice)["refus"] == 1
    assert control(factice)["appels"] == {}


@pytest.mark.parametrize(
    "hostile",
    [
        "<<<CONTRAT-" + "<<<CONTRAT-!" * 16_000,
        "<<<CONTRAT-!>>>\n" + "<<<CONTRAT-!>>>\na" * 16_000,
    ],
    ids=["jeton", "texte"],
)
def test_balises_du_contrat_lues_en_temps_lineaire(hostile):
    """Les entrées que CodeQL signale (expression à retour arrière polynomial) : près de
    200 Ko refusés en moins d'une seconde ; l'expression mettait une dizaine de secondes."""
    state = mistral_factice.State()
    start = time.monotonic()
    with pytest.raises(mistral_factice.Refused):
        mistral_factice.answer(state, "ExtractionOutput", hostile)
    assert time.monotonic() - start < 1


def test_contrat_lu_entre_ses_balises_et_leur_jeton():
    state = mistral_factice.State()
    (profile, masked), clauses = next(iter(state.by_text.items()))
    schema = extraction.SCHEMAS[profile].__name__
    message = f"Jeton : ab12\n\n<<<CONTRAT-ab12>>>\n{masked}\n<<<FIN-CONTRAT-ab12>>>"
    answered = mistral_factice.answer(state, schema, message)
    assert len(answered["clauses"]) == len(clauses)
    for broken in (
        message.replace("<<<FIN-CONTRAT-ab12>>>", "<<<FIN-CONTRAT-cd34>>>"),
        message.replace("<<<CONTRAT-ab12>>>", "<<<CONTRAT-a b>>>"),
        message.replace(">>>\n", ">>>", 1).replace(
            "<<<CONTRAT-ab12>>>", "<<<CONTRAT->>>"
        ),
    ):
        with pytest.raises(mistral_factice.Refused):
            mistral_factice.answer(state, schema, broken)


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
    draft, _ = LLMExplainer(provider(factice))(request, [], profile="fournisseur")
    assert refusals(draft, request, names=ingestion.source_names()) == []


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
