"""Serveur MCP (ADR 007) : troisième porte sur le service des contrats, par un vrai client
MCP en mémoire (JSON-RPC, poignée de main `initialize`).

- Quatre outils, chacun une méthode du service, annotations posées explicitement ; aucun
  outil de décision (ni revue humaine, ni levée de blocage, ni expiration, ni relance).
- Une analyse lancée par MCP scelle son canal (mcp) ; les quatre yeux s'appliquent comme
  ailleurs.
- Injection indirecte : le contrat piégé du jeu ne laisse passer aucune ligne de son
  texte hors d'une enveloppe délimitée, signalée comme contenu non fiable.
- Mode démonstration : sans clé, sans coût, sans base.
- Erreurs explicites, sans analyse lancée ; une exception imprévue ne livre que son type.
"""

import ast
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from demo_set import load as load_expected
from doubles import CONTRACT_TEXT, FIXED_NOW
from mcp_helpers import (
    MCP_ACTOR,
    call,
    error_text,
    escaped,
    leaks,
    outside,
    seen,
    session,
    tools,
    written_by_code,
)
from web_helpers import CONFIG, PENDING_TEXT, memory_service

from cdg import cli
from cdg.adapters.mcp import server as mcp_server
from cdg.application import demo_set, saisie
from cdg.application.saisie import InputError
from cdg.application.service import FourEyesRefused
from cdg.domain import profiles
from cdg.domain.authorization import Actor

_, _EXPECTED = load_expected()
EXPECTED = {c.id: c for c in _EXPECTED}
FOURNISSEUR = [cid for cid, c in EXPECTED.items() if c.profile == "fournisseur"]
PIEGE = "demo-11-piege-injection"
INJECTED_LINE = EXPECTED[PIEGE].injected.split("\n")[1]
NAMES = {"analyser_contrat", "lister_contrats", "consulter_dossier", "verifier_journal"}
ISS = "https://idp.example.org"
RELECTEUR = Actor(canal="interface", authentifie=True, iss=ISS, sub="sub-relecteur")
OPERATEUR = Actor(canal="cli", authentifie=False, operateur="relecteur-1")
MCP_SOURCES = Path(__file__).resolve().parents[1] / "src" / "cdg" / "adapters" / "mcp"


def forbidden(*args, **kwargs):
    raise AssertionError("le mode démonstration n'appelle ni LLM ni PostgreSQL")


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.delenv(cli.COMMIT_VAR, raising=False)  # poste sans commit fourni
    monkeypatch.setattr(cli, "build_provider", forbidden)
    monkeypatch.setattr(cli.conninfo, "app_conninfo", forbidden)
    monkeypatch.setattr(cli.conninfo, "admin_conninfo", forbidden)
    service = cli.demo_service(CONFIG)
    return mcp_server.create_server(service, actor=MCP_ACTOR, demo=True), service


@pytest.fixture
def real():
    """Mode réel, sur le service en mémoire des tests (vrai graphe, doublures du LLM et
    du CRAG)."""
    service = memory_service()
    return mcp_server.create_server(service, actor=MCP_ACTOR, demo=False), service


def leaked(service, thread_id: str, result) -> list[str]:
    """Fenêtres du texte masqué trouvées dans ce que voit l'assistant, hors des
    enveloppes et des textes écrits par le code."""
    allowed = written_by_code(result.structured_content)
    masked = service.dossier(thread_id)["texte_masque"]
    return leaks(masked, outside(seen(result)), allowed)


# --- outils, annotations, schémas ----------------------------------------------------------

READ_ONLY = {
    "read_only_hint": True,
    "destructive_hint": False,
    "idempotent_hint": True,
    "open_world_hint": False,
}


@pytest.mark.parametrize("mode", ["demo", "real"])
def test_quatre_outils_et_leurs_annotations(mode, request):
    server, _ = request.getfixturevalue(mode)
    found = tools(server)
    assert set(found) == NAMES
    hints = {
        name: tool.annotations.model_dump(exclude={"title"})
        for name, tool in found.items()
    }
    assert hints == {
        "analyser_contrat": {
            "read_only_hint": False,
            "destructive_hint": False,
            "idempotent_hint": False,
            # mode réel : appels au fournisseur LLM ; démonstration : rien hors du processus
            "open_world_hint": mode == "real",
        },
        "lister_contrats": READ_ONLY,
        "consulter_dossier": READ_ONLY,
        "verifier_journal": READ_ONLY,
    }
    assert all(tool.title and tool.description for tool in found.values())
    assert all(tool.output_schema for tool in found.values())


def test_aucun_outil_de_decision(real):
    server, _ = real
    for tool in tools(server).values():
        words = f"{tool.name} {tool.title}".lower()
        for forbidden_word in ("decid", "décid", "resume", "tranch", "expir", "relanc"):
            assert forbidden_word not in words, tool.name


def test_adaptateur_n_appelle_aucune_methode_de_decision():
    """Ni revue humaine (decide, resume), ni expiration, ni relance, ni reprise : aucun de
    ces noms n'est seulement référencé par l'adaptateur."""
    decisions = {"decide", "resume", "expire", "relaunch", "resume_interrupted"}
    sources = sorted(MCP_SOURCES.glob("*.py"))
    assert {p.name for p in sources} >= {"server.py", "presentation.py"}
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        used |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert not used & decisions, (path.name, used & decisions)


def test_schema_enumere_les_contrats_du_jeu(real):
    server, _ = real
    schema = tools(server)["analyser_contrat"].input_schema
    choice = schema["properties"]["contrat_du_jeu"]
    enums = [option["enum"] for option in choice["anyOf"] if "enum" in option]
    assert enums == [sorted(demo_set.load()[1])]
    assert set(schema["properties"]) == {
        "profil",
        "contrat_du_jeu",
        "texte",
        "parties",
        "identifiant",
        "date_analyse",
    }


def test_schema_exige_un_profil_enumere(real):
    """Le profil est obligatoire, sans défaut, et limité aux profils connus."""
    server, _ = real
    schema = tools(server)["analyser_contrat"].input_schema
    assert schema["required"] == ["profil"]
    profil = schema["properties"]["profil"]
    # un Literal à une seule valeur s'écrit `const` en JSON Schema, `enum` à plusieurs
    assert profil.get("enum", [profil.get("const")]) == list(profiles.PROFILES)
    assert "default" not in profil
    assert "fournisseur" in profil["description"]


def test_schemas_de_sortie_en_liste_blanche(real):
    server, _ = real
    found = tools(server)
    dossier = found["consulter_dossier"].output_schema
    assert {"contenu_non_fiable", "references", "domaines"} <= set(
        dossier["properties"]
    )
    assert "texte_masque" not in json.dumps(dossier)
    assert set(found["lister_contrats"].output_schema["properties"]) == {"contrats"}
    assert "conforme" in found["verifier_journal"].output_schema["properties"]


def test_instructions_du_serveur(real, demo):
    for server, _ in (real, demo):
        text = server.instructions
        assert "aucun outil de décision" in text
        assert "non fiable" in text
        assert "ni par la CLI" in text  # ni par un autre chemin de l'assistant
    assert "démonstration" in demo[0].instructions
    assert "démonstration" not in real[0].instructions


def test_acteur_du_canal_mcp_exige():
    with pytest.raises(ValueError, match="canal mcp"):
        mcp_server.create_server(memory_service(), actor=OPERATEUR, demo=False)


# --- démonstration ------------------------------------------------------------------------


# Contrats du profil fournisseur seulement, jusqu'à la tâche 14 du plan de la PR 2 des
# profils (mode démonstration des deux profils) : le test analyse chaque contrat sous
# « fournisseur ».
@pytest.mark.parametrize("contract_id", sorted(FOURNISSEUR))
def test_demo_chaque_contrat_du_jeu_rend_l_issue_attendue(demo, contract_id):
    server, _ = demo
    contract = EXPECTED[contract_id]
    result = call(
        server,
        "analyser_contrat",
        {
            "profil": "fournisseur",
            "contrat_du_jeu": contract_id,
            "identifiant": contract_id,
        },
    )
    assert not result.is_error, seen(result)
    fiche = result.structured_content
    assert fiche["thread_id"] == contract_id
    if contract.rejected:
        assert (fiche["etat"], bool(fiche["rejet"])) == ("rejete", True)
        return
    assert fiche["decision_proposee"] == contract.expected["proposed_decision"]
    if contract.human is None:
        assert (fiche["etat"], fiche["decision_finale"]) == (
            "termine",
            contract.expected["final_decision"],
        )
    else:
        assert (fiche["etat"], fiche["decision_finale"]) == ("en_attente", None)
        assert fiche["revue_humaine_en_attente"]
    assert fiche["analyse_par"] == MCP_ACTOR.model_dump(mode="json")


def test_demo_texte_libre_refuse(demo):
    server, service = demo
    result = call(
        server, "analyser_contrat", {"profil": "fournisseur", "texte": CONTRACT_TEXT}
    )
    assert "contrats du jeu seulement" in error_text(result)
    assert service.contracts() == []


def test_analyse_du_jeu_parties_masquees(demo):
    server, service = demo
    contract_id = "demo-06-no-go-conseil"
    result = call(
        server,
        "analyser_contrat",
        {"profil": "fournisseur", "contrat_du_jeu": contract_id, "parties": ["X"]},
    )
    fiche = result.structured_content
    assert fiche["masquage"]["PARTIE"] > 0
    raw = service.engine.values(fiche["thread_id"])["raw_text"]
    assert all(party not in raw for party in EXPECTED[contract_id].parties)
    assert fiche["thread_id"].startswith(f"{contract_id}-")  # identifiant par défaut


def test_analyse_d_un_texte_fourni_masque(real):
    server, service = real
    text = f"{CONTRACT_TEXT}\nContact : achats@example.org, Alpha Synthétique.\n"
    result = call(
        server,
        "analyser_contrat",
        {
            "profil": "fournisseur",
            "texte": text,
            "parties": ["Alpha Synthétique"],
            "identifiant": "c-1",
        },
    )
    fiche = result.structured_content
    assert fiche["masquage"] == {"EMAIL": 1, "PARTIE": 1}
    raw = service.engine.values("c-1")["raw_text"]
    assert "achats@example.org" not in raw and "Alpha Synthétique" not in raw
    assert "achats@example.org" not in seen(result)


# --- canal scellé et quatre yeux -------------------------------------------------------------


def answer(actor: Actor) -> dict:
    return {"decision": "GO", "acteur": actor.model_dump(mode="json"), "reason": "revu"}


def test_analyse_mcp_scellee_avec_son_canal(real):
    server, service = real
    result = call(
        server,
        "analyser_contrat",
        {"profil": "fournisseur", "texte": PENDING_TEXT, "identifiant": "c-attente"},
    )
    assert result.structured_content["etat"] == "en_attente"
    # revue dans l'interface authentifiée : autre canal, refusée (quatre yeux)
    with pytest.raises(FourEyesRefused, match="autre canal"):
        service.decide("c-attente", answer(RELECTEUR))
    # outil local, hors du cluster, comme pour une analyse de la CLI
    service.decide("c-attente", answer(OPERATEUR))
    [sealed] = [
        e for e in service.audit_store().entries() if e.thread_id == "c-attente"
    ]
    assert sealed.record["analyse_par"] == MCP_ACTOR.model_dump(mode="json")
    assert sealed.record["decision"]["human"]["acteur"] == OPERATEUR.model_dump(
        mode="json"
    )
    assert service.verify().ok
    assert service.replay("c-attente")["identique"]


def test_ni_decision_ni_expiration_par_le_canal_mcp_meme_hors_outils(real):
    """Défense en profondeur : même sans outil, le service refuse une décision ou une
    expiration du canal mcp, avant le graphe ; le contrat reste en attente."""
    server, service = real
    call(
        server,
        "analyser_contrat",
        {"profil": "fournisseur", "texte": PENDING_TEXT, "identifiant": "c-mcp"},
    )
    with pytest.raises(FourEyesRefused, match="jamais de décision"):
        service.decide("c-mcp", answer(MCP_ACTOR))
    with pytest.raises(FourEyesRefused, match="jamais de décision"):
        service.expire(timedelta(0), MCP_ACTOR)
    assert service.contracts(pending_only=True)[0]["thread_id"] == "c-mcp"
    assert service.journal() == []
    # second contrôle, dans le graphe : la réponse est refusée et redemandée
    later = FIXED_NOW + timedelta(days=30)
    [expired] = service.engine.expire(timedelta(0), later, MCP_ACTOR)
    resumed = service.engine.resume("c-mcp", answer(MCP_ACTOR))
    for status in (expired, resumed):
        assert status["statut"] == "suspendu" and status["final_decision"] is None
        assert "jamais de décision" in status["demande"]["error"]
    assert service.journal() == []


# --- lister, consulter, vérifier ----------------------------------------------------------------


def test_lister_contrats(real):
    server, _ = real
    call(
        server,
        "analyser_contrat",
        {"profil": "fournisseur", "texte": PENDING_TEXT, "identifiant": "c-a"},
    )
    call(
        server,
        "analyser_contrat",
        {"profil": "fournisseur", "texte": CONTRACT_TEXT, "identifiant": "c-b"},
    )
    every = call(server, "lister_contrats").structured_content["contrats"]
    assert {r["thread_id"]: r["etat"] for r in every} == {
        "c-a": "en_attente",
        "c-b": "termine",
    }
    pending = call(server, "lister_contrats", {"en_attente": True})
    assert [r["thread_id"] for r in pending.structured_content["contrats"]] == ["c-a"]


def test_consulter_dossier(real):
    server, _ = real
    call(
        server,
        "analyser_contrat",
        {"profil": "fournisseur", "texte": CONTRACT_TEXT, "identifiant": "c-1"},
    )
    result = call(server, "consulter_dossier", {"thread_id": "c-1"})
    dossier = result.structured_content
    assert (dossier["etat"], dossier["decision_finale"]) == ("termine", "GO")
    assert dossier["explication"]["source"] == "gabarit"
    assert dossier["explication"]["synthese"]
    assert len(dossier["domaines"]) == 4
    assert dossier["contenu_non_fiable"] == []
    assert dossier["empreintes"]["decision"] and dossier["empreintes"]["chaine"]
    # le texte JSON de la réponse redit le contenu structuré
    assert json.loads(result.content[0].text) == dossier


def test_verifier_journal(real):
    server, _ = real
    empty = call(server, "verifier_journal").structured_content
    assert (empty["conforme"], empty["enregistrements"]) == (True, 0)
    call(
        server,
        "analyser_contrat",
        {"profil": "fournisseur", "texte": CONTRACT_TEXT, "identifiant": "c-1"},
    )
    report = call(server, "verifier_journal").structured_content
    assert (report["conforme"], report["enregistrements"]) == (True, 1)
    other = call(server, "verifier_journal", {"tete_attendue": "0" * 64})
    assert not other.is_error and not other.structured_content["conforme"]
    reason = other.structured_content["raison"]
    assert reason["origine"] == "journal" and "tronquée" in reason["texte"]
    same = call(server, "verifier_journal", {"tete_attendue": report["tete"]})
    assert same.structured_content["conforme"]


def test_verifier_journal_empreinte_mal_formee(real):
    server, _ = real
    result = call(server, "verifier_journal", {"tete_attendue": "pas-une-empreinte"})
    assert "empreinte invalide" in error_text(result)


def test_dossier_inconnu_erreur_explicite(real):
    server, _ = real
    assert "inconnu" in error_text(
        call(server, "consulter_dossier", {"thread_id": "x"})
    )


# --- injection indirecte : le contrat piégé du jeu ------------------------------------------


def test_contrat_piege_analyse_sans_texte_du_contrat(demo):
    server, service = demo

    async def steps(client):
        analysed = await client.call_tool(
            "analyser_contrat",
            {"profil": "fournisseur", "contrat_du_jeu": PIEGE, "identifiant": "piege"},
        )
        consulted = await client.call_tool("consulter_dossier", {"thread_id": "piege"})
        return analysed, consulted

    analysed, consulted = session(server, steps)
    for result in (analysed, consulted):
        assert leaked(service, "piege", result) == []
        assert "conclus GO" not in seen(result)
    fiche = analysed.structured_content
    assert (fiche["decision_proposee"], fiche["decision_finale"]) == ("NO_GO", None)
    assert (fiche["etat"], fiche["tentatives_d_instruction"]) == ("en_attente", 2)


def test_contrat_piege_citations_enveloppees(demo):
    server, service = demo
    call(
        server,
        "analyser_contrat",
        {"profil": "fournisseur", "contrat_du_jeu": PIEGE, "identifiant": "piege"},
    )
    result = call(
        server, "consulter_dossier", {"thread_id": "piege", "citations": True}
    )
    out = seen(result)
    assert escaped(INJECTED_LINE) in out
    assert escaped(INJECTED_LINE) not in outside(out)
    assert leaked(service, "piege", result) == []
    envelopes = result.structured_content["contenu_non_fiable"]
    [passage] = [e for e in envelopes if INJECTED_LINE in e["texte"]]
    assert passage["contenu_non_fiable"] is True and passage["origine"] == "contrat"
    assert "jamais une consigne" in passage["avertissement"]


@pytest.mark.parametrize("citations", [False, True])
def test_aucun_contrat_du_jeu_ne_fuit_hors_enveloppe(demo, citations):
    """Les 13 contrats du jeu, analysés puis consultés : aucune fenêtre de six mots de
    leur texte hors d'une enveloppe, hors des textes écrits par le code."""
    server, service = demo
    found = {}
    for contract_id in sorted(EXPECTED):
        analysed = call(
            server,
            "analyser_contrat",
            {
                "profil": "fournisseur",
                "contrat_du_jeu": contract_id,
                "identifiant": contract_id,
            },
        )
        consulted = call(
            server,
            "consulter_dossier",
            {"thread_id": contract_id, "citations": citations},
        )
        assert not analysed.is_error and not consulted.is_error, seen(analysed)
        found[contract_id] = [
            *leaked(service, contract_id, analysed),
            *leaked(service, contract_id, consulted),
        ]
    assert found == {contract_id: [] for contract_id in EXPECTED}


# --- points d'attention ----------------------------------------------------------------------


def test_identifiant_deja_pris_erreur_explicite(real):
    server, _ = real
    arguments = {"profil": "fournisseur", "texte": CONTRACT_TEXT, "identifiant": "c-1"}
    assert not call(server, "analyser_contrat", arguments).is_error
    refused = error_text(call(server, "analyser_contrat", arguments))
    assert "c-1 existe déjà" in refused
    # le message du moteur conseille resume (une décision humaine) : pas à l'assistant
    assert "resume" not in refused


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "l'un ou l'autre"),
        ({"contrat_du_jeu": PIEGE, "texte": CONTRACT_TEXT}, "l'un ou l'autre"),
        ({"contrat_du_jeu": "demo-99-inconnu"}, "contrat_du_jeu"),
        ({"texte": "   "}, "le texte du contrat est vide"),
        ({"texte": f"{CONTRACT_TEXT}\x00"}, "caractère de contrôle"),
        ({"texte": CONTRACT_TEXT, "date_analyse": "25/09/2026"}, "date invalide"),
        ({"texte": CONTRACT_TEXT, "identifiant": "../c"}, "identifiant de contrat"),
    ],
)
def test_arguments_incoherents_erreur_sans_analyse(real, arguments, message):
    server, service = real
    given = {"profil": "fournisseur", **arguments}
    assert message in error_text(call(server, "analyser_contrat", given))
    assert service.contracts() == []


HOSTILE_START = "PASSAGE-HOSTILE-DEBUT"
HOSTILE_END = "PASSAGE-HOSTILE-FIN"
HOSTILE_PARTY = "Zeta Synthétique"
# arguments d'un appel dont le texte et les parties ne doivent jamais revenir à l'assistant
# par une erreur ; l'ordre des clés compte : la validation répète le début et la fin du
# dictionnaire (14 premiers et 22 derniers caractères), donc le début du texte, puis
# sa fin ou les parties
HOSTILE_ARGUMENTS = {
    "texte": f"{HOSTILE_START} "
    + "Le présent contrat lie les parties. " * 60
    + HOSTILE_END,
    "parties": [HOSTILE_PARTY],
}


def fragments(secret: str, size: int = 8) -> list[str]:
    """Tous les morceaux de `size` caractères d'un secret : une fuite partielle se voit."""
    return [secret[i : i + size] for i in range(len(secret) - size + 1)]


def fragments_seen(result) -> list[str]:
    """Morceaux du texte hostile et des parties présents dans ce que voit l'assistant."""
    shown = seen(result)
    return [
        piece
        for secret in (HOSTILE_START, HOSTILE_END, HOSTILE_PARTY)
        for piece in fragments(secret)
        if piece in shown
    ]


@pytest.mark.parametrize("given", [["texte"], ["texte", "parties"]])
def test_analyser_contrat_sans_profil_refuse(real, given):
    """Assistant configuré avant la mise à jour : résultat d'erreur qui nomme le profil
    manquant, rien n'est analysé, aucun contrat créé. Le message est écrit par le code :
    ni le texte du contrat, ni les noms des parties ne reviennent à l'assistant (la
    validation du SDK répète le début et la fin des arguments reçus)."""
    server, service = real
    arguments = {name: HOSTILE_ARGUMENTS[name] for name in given}
    result = call(server, "analyser_contrat", arguments)
    assert error_text(result) == (
        "argument obligatoire manquant de l'outil analyser_contrat : profil"
    )
    assert fragments_seen(result) == []
    assert service.contracts() == []


PROFIL_REFUSE = "profil absent ou inconnu ; profils : fournisseur, sous_traitance"
PYDANTIC_TEXTS = ("validation error", "errors.pydantic.dev", "input_value")


def test_message_du_profil_refuse_est_celui_de_la_saisie():
    """Le message attendu de l'outil est celui de la saisie commune (interface web)."""
    with pytest.raises(InputError) as refused:
        saisie.profile("")
    assert str(refused.value) == PROFIL_REFUSE


@pytest.mark.parametrize(
    "profil", ["", "Fournisseur", "inexistant", None, 3, ["fournisseur"]]
)
def test_analyser_contrat_profil_inconnu_refuse(real, profil):
    """Profil présent mais invalide (inconnu, vide, d'une autre casse, `None`, d'un autre
    type) : erreur de l'outil, avec le message de la saisie commune, avant la validation
    du SDK (spec, « Erreurs » : sans message d'exception). Ni le message de pydantic, ni
    la valeur reçue, ni le texte, ni les parties ; rien n'est analysé."""
    server, service = real
    result = call(server, "analyser_contrat", {"profil": profil, **HOSTILE_ARGUMENTS})
    text = error_text(result)
    assert text == PROFIL_REFUSE
    shown = seen(result)
    for pydantic_text in PYDANTIC_TEXTS:
        assert pydantic_text not in shown
    if profil != "":  # la chaîne vide est dans tout texte
        for received in {str(profil), repr(profil), json.dumps(profil)}:
            assert received not in text
    assert fragments_seen(result) == []
    assert service.contracts() == []


def test_argument_obligatoire_manquant_d_un_autre_outil(real):
    """La règle vaut pour tous les outils : `thread_id` de la consultation."""
    server, _ = real
    result = call(server, "consulter_dossier", {"citations": True})
    assert error_text(result) == (
        "argument obligatoire manquant de l'outil consulter_dossier : thread_id"
    )


def refused_by_middleware(parameters, required, tool, arguments):
    """Le middleware seul, sur une requête `tools/call` : (réponse, suite appelée)."""
    request = SimpleNamespace(
        method="tools/call", params={"name": tool, "arguments": arguments}
    )
    reached = []

    async def call_next(_):
        reached.append(tool)
        return "suite"

    middleware = mcp_server._known_arguments(parameters, required)
    return anyio.run(middleware, request, call_next), reached


def test_middleware_nomme_tous_les_arguments_manquants_sans_valeur():
    parameters = {"outil": frozenset({"a", "b", "c"})}
    required = {"outil": frozenset({"a", "b"})}
    result, reached = refused_by_middleware(
        parameters, required, "outil", {"c": "VALEUR-RECUE"}
    )
    assert reached == [] and result.is_error
    assert result.content[0].text == (
        "argument obligatoire manquant de l'outil outil : a, b"
    )
    assert "VALEUR-RECUE" not in seen(result)


@pytest.mark.parametrize("arguments", [None, {}])
def test_middleware_sans_arguments_refuse_aussi(arguments):
    """Requête sans `arguments` : traitée comme un dictionnaire vide."""
    result, reached = refused_by_middleware(
        {"outil": frozenset({"a"})}, {"outil": frozenset({"a"})}, "outil", arguments
    )
    assert reached == [] and result.is_error


def test_middleware_laisse_passer_une_requete_complete_ou_d_un_autre_type():
    parameters, required = {"outil": frozenset({"a", "b"})}, {"outil": frozenset({"a"})}
    assert refused_by_middleware(parameters, required, "outil", {"a": 1})[1] == [
        "outil"
    ]
    assert refused_by_middleware(parameters, required, "inconnu", {})[1] == ["inconnu"]
    # un argument inconnu garde son message, avant le contrôle des obligatoires
    result, _ = refused_by_middleware(parameters, required, "outil", {"z": 1})
    assert result.content[0].text == "argument inconnu de l'outil outil : z"


@pytest.mark.parametrize("profil", ["Fournisseur", None, ["fournisseur"]])
def test_middleware_refuse_un_profil_inconnu_sans_aller_plus_loin(profil):
    """Profil présent mais inconnu : refusé par le middleware, avant la validation du
    SDK, avec le message de la saisie commune ; un profil connu passe."""
    parameters = {"outil": frozenset({"profil", "a"})}
    required = {"outil": frozenset({"profil"})}
    result, reached = refused_by_middleware(
        parameters, required, "outil", {"profil": profil, "a": "VALEUR-RECUE"}
    )
    assert reached == [] and result.is_error
    assert result.content[0].text == PROFIL_REFUSE
    assert "VALEUR-RECUE" not in seen(result)
    passed = refused_by_middleware(
        parameters, required, "outil", {"profil": "fournisseur"}
    )
    assert passed[1] == ["outil"]


def test_profil_passe_par_la_saisie_commune(real, monkeypatch):
    """En plus du schéma, le profil est validé par `saisie.profile`, comme à l'interface
    web ; un refus de la saisie est une erreur d'outil explicite, sans analyse."""
    server, service = real
    seen_by_input = []
    genuine = saisie.profile

    def spy(raw):
        seen_by_input.append(raw)
        return genuine(raw)

    monkeypatch.setattr(saisie, "profile", spy)
    arguments = {"profil": "fournisseur", "texte": CONTRACT_TEXT, "identifiant": "c-1"}
    assert not call(server, "analyser_contrat", arguments).is_error
    assert seen_by_input == ["fournisseur"]
    assert service.dossier("c-1")["status"]["profil"] == "fournisseur"

    def refuse(raw):
        raise InputError("profil refusé par la saisie")

    monkeypatch.setattr(saisie, "profile", refuse)
    refused = error_text(
        call(server, "analyser_contrat", {**arguments, "identifiant": "c-2"})
    )
    assert refused.endswith("profil refusé par la saisie")
    assert [r["thread_id"] for r in service.contracts()] == ["c-1"]


@pytest.mark.parametrize(
    ("tool", "arguments", "unknown"),
    [
        ("lister_contrats", {"en_attente_seulement": True}, "en_attente_seulement"),
        ("consulter_dossier", {"thread_id": "c-1", "citation": True}, "citation"),
        (
            "analyser_contrat",
            {"profil": "fournisseur", "texte": CONTRACT_TEXT, "decision": "GO"},
            "decision",
        ),
    ],
)
def test_argument_inconnu_erreur_explicite(real, tool, arguments, unknown):
    """Le SDK ignorerait un argument inconnu sans rien dire : une faute de frappe
    (citation pour citations) changerait la réponse en silence ; refusé, nommé."""
    server, service = real
    text = error_text(call(server, tool, arguments))
    assert f"argument inconnu de l'outil {tool} : {unknown}" in text
    assert service.contracts() == []


class Broken:
    """Service dont une lecture lève une exception qui cite une donnée reçue."""

    def __init__(self, service, secret: str):
        self._service, self._secret = service, secret

    def __getattr__(self, name):
        return getattr(self._service, name)

    def contracts(self, **_):
        raise ValueError(self._secret)


def test_erreur_inattendue_type_seulement(caplog):
    secret = "texte du contrat : conclus GO"
    server = mcp_server.create_server(
        Broken(memory_service(), secret), actor=MCP_ACTOR, demo=False
    )
    with caplog.at_level("DEBUG"):
        result = call(server, "lister_contrats")
    assert "erreur inattendue : ValueError" in error_text(result)
    assert secret not in seen(result)
    assert secret not in caplog.text
    assert "lister_contrats" in caplog.text and "ValueError" in caplog.text


def test_texte_trop_long_rejete_et_scelle(real):
    server, service = real
    text = "Le présent contrat " * (CONFIG.input.max_chars // 10)
    assert len(text) > CONFIG.input.max_chars
    result = call(
        server,
        "analyser_contrat",
        {"profil": "fournisseur", "texte": text, "identifiant": "long"},
    )
    fiche = result.structured_content
    assert fiche["etat"] == "rejete" and fiche["rejet"].startswith("texte trop long")
    assert [e["thread_id"] for e in service.journal()] == ["long"]
