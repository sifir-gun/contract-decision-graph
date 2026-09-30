"""Autorisation dans l'interface authentifiée (PR D2, ADR 005).

- Rôles tirés des groupes du jeton vérifié : l'analyste analyse, le relecteur tranche et
  expire, les deux lisent ; sans rôle, 403 partout, sauf la déconnexion (et les
  ressources de la page d'erreur). Chaque refus est tracé au journal des accès.
- Quatre yeux, premier contrôle (le service, avant le graphe) : 403, tracé.
- Second facteur du relecteur, réglable : sans preuve, 403 sur ses actions, tracé.
"""

import logging

import pytest
from doubles import (
    ACTEUR_ANALYSTE,
    ANALYSTE,
    ISSUER,
    OPERATEUR,
    RELECTEUR,
    FakeVerifier,
    answer,
)
from fastapi.testclient import TestClient
from web_helpers import PENDING_TEXT, memory_service

from cdg.adapters.web.app import Authentication, create_app
from cdg.adapters.web.presentation import actor_label, human_label
from cdg.domain.authorization import Actor, SecondFactor
from cdg.domain.identity import Identity

PUBLIC = "https://cdg.example.org"
ROLES = {"analyste": ("cdg-analystes",), "relecteur": ("cdg-relecteurs",)}
POLYVALENT = Identity(
    ISSUER, "sub-polyvalent", ("cdg-analystes", "cdg-relecteurs"), "polyvalent.affiche"
)
SANS_ROLE = Identity(ISSUER, "sub-sans-role", ("autre",), "sans.role")
RELECTEUR_MFA = Identity(
    ISSUER, "sub-relecteur-mfa", ("cdg-relecteurs",), "mfa.affiche", amr=("pwd", "mfa")
)
TOKENS = {
    "jeton-analyste": ANALYSTE,
    "jeton-relecteur": RELECTEUR,
    "jeton-polyvalent": POLYVALENT,
    "jeton-sans-role": SANS_ROLE,
    "jeton-relecteur-mfa": RELECTEUR_MFA,
}
TOKEN = 'name="csrf" value="'


@pytest.fixture(autouse=True)
def _journal_des_acces(caplog):
    caplog.set_level(logging.INFO, logger="cdg.acces")


def events(caplog) -> list[dict]:
    return [r.acces for r in caplog.records if r.name == "cdg.acces"]


def bearer(name: str) -> dict[str, str]:
    return {"authorization": f"Bearer jeton-{name}", "origin": PUBLIC}


SANS_SECOND_FACTEUR = SecondFactor()


def interface(second_factor=SANS_SECOND_FACTEUR, service=None) -> TestClient:
    app = create_app(
        service or memory_service(),
        authentication=Authentication(
            verifier=FakeVerifier(TOKENS),
            public_origin=PUBLIC,
            client_id="cdg-interface",
            roles=ROLES,
            second_factor=second_factor,
        ),
    )
    return TestClient(app, base_url="https://127.0.0.1:8000", follow_redirects=False)


def form_token(web: TestClient, who: str, page: str = "/analyse") -> str:
    return web.get(page, headers=bearer(who)).text.split(TOKEN)[1].split('"')[0]


def analyse(web: TestClient, who: str, identifier: str = "c-attente"):
    return web.post(
        "/analyse",
        data={
            "csrf": form_token(web, who),
            "source": "texte",
            "texte": PENDING_TEXT,
            "identifiant": identifier,
        },
        headers=bearer(who),
    )


def decide(web: TestClient, who: str, thread: str = "c-attente"):
    return web.post(
        f"/contrats/{thread}/decision",
        data={"csrf": form_token(web, who), "decision": "NO_GO", "motif": "revu"},
        headers=bearer(who),
    )


def expire(web: TestClient, who: str):
    return web.post(
        "/administration/expiration",
        data={"csrf": form_token(web, who), "heures": "24", "confirme": "oui"},
        headers=bearer(who),
    )


# --- rôles -------------------------------------------------------------------------------


def test_sans_role_403_partout_sauf_la_deconnexion(caplog):
    web = interface()
    for path in ("/", "/analyse", "/journal", "/administration"):
        response = web.get(path, headers=bearer("sans-role"))
        assert response.status_code == 403, path
        assert "Accès refusé" in response.text
    assert web.get("/static/style.css", headers=bearer("sans-role")).status_code == 200
    denied = [e for e in events(caplog) if e["evenement"] == "acces_interdit"]
    assert denied[0] == {
        "evenement": "acces_interdit",
        "motif": "role_manquant",
        "role": "analyste,relecteur",
        "iss": ISSUER,
        "sub": "sub-sans-role",
        "methode": "GET",
        "chemin": "/",
        "statut": 403,
    }
    token = web.get("/", headers=bearer("sans-role")).text.split(TOKEN)[1].split('"')[0]
    logout = web.post("/deconnexion", data={"csrf": token}, headers=bearer("sans-role"))
    assert logout.status_code == 200


def test_analyste_analyse_mais_ne_tranche_ni_n_expire(caplog):
    web = interface()
    assert analyse(web, "analyste").status_code == 303
    assert decide(web, "analyste").status_code == 403
    assert expire(web, "analyste").status_code == 403
    roles = [e["role"] for e in events(caplog) if e["evenement"] == "acces_interdit"]
    assert roles == ["relecteur", "relecteur"]


def test_relecteur_tranche_l_analyse_d_un_autre_mais_n_analyse_pas():
    web = interface()
    assert analyse(web, "relecteur", "c-refus").status_code == 403
    assert analyse(web, "analyste").status_code == 303
    assert decide(web, "relecteur").status_code == 303


def test_lecture_ouverte_aux_deux_roles():
    web = interface()
    analyse(web, "analyste")
    for who in ("analyste", "relecteur"):
        assert web.get("/contrats/c-attente", headers=bearer(who)).status_code == 200


def test_formulaire_de_revue_montre_au_seul_relecteur():
    web = interface()
    analyse(web, "analyste")
    assert (
        'id="revue"'
        not in web.get("/contrats/c-attente", headers=bearer("analyste")).text
    )
    assert (
        'id="revue"' in web.get("/contrats/c-attente", headers=bearer("relecteur")).text
    )


# --- quatre yeux ---------------------------------------------------------------------------


def test_quatre_yeux_celui_qui_a_analyse_ne_tranche_pas(caplog):
    service = memory_service()
    web = interface(service=service)
    assert analyse(web, "polyvalent").status_code == 303
    refused = decide(web, "polyvalent")
    assert refused.status_code == 403
    assert "quatre yeux" in refused.text
    [event] = [e for e in events(caplog) if e["evenement"] == "quatre_yeux_refuse"]
    assert event == {
        "evenement": "quatre_yeux_refuse",
        "iss": ISSUER,
        "sub": "sub-polyvalent",
        "thread_id": "c-attente",
        "methode": "POST",
        "chemin": "/contrats/c-attente/decision",
        "statut": 403,
    }
    assert service.dossier("c-attente")["etat"] == "en_attente"  # rien tranché
    assert decide(web, "relecteur").status_code == 303


def test_acteurs_scelles_sans_nom():
    service = memory_service()
    web = interface(service=service)
    analyse(web, "analyste")
    decide(web, "relecteur")
    status = service.dossier("c-attente")["status"]
    assert status["analyse_par"] == ACTEUR_ANALYSTE.model_dump(mode="json")
    assert status["human"]["acteur"]["sub"] == RELECTEUR.subject
    assert "relecteur.affiche" not in str(status)


# --- second facteur ------------------------------------------------------------------------


def test_second_facteur_exige_pour_trancher_et_expirer(caplog):
    web = interface(second_factor=SecondFactor(amr=("mfa",)))
    analyse(web, "analyste")
    assert (
        web.get("/contrats/c-attente", headers=bearer("relecteur")).status_code == 200
    )
    assert decide(web, "relecteur").status_code == 403
    assert expire(web, "relecteur").status_code == 403
    missing = [e for e in events(caplog) if e["evenement"] == "second_facteur_manquant"]
    assert [e["chemin"] for e in missing] == [
        "/contrats/c-attente/decision",
        "/administration/expiration",
    ]
    assert missing[0]["sub"] == RELECTEUR.subject
    assert decide(web, "relecteur-mfa").status_code == 303


def test_second_facteur_non_exige_par_defaut():
    web = interface()
    analyse(web, "analyste")
    assert decide(web, "relecteur").status_code == 303


# --- acteurs lisibles, jamais un nom ----------------------------------------------------------


@pytest.mark.parametrize(
    ("actor", "label"),
    [
        (None, "inconnu (analyse antérieure aux rôles)"),
        (ACTEUR_ANALYSTE, f"utilisateur {ANALYSTE.subject} (interface authentifiée)"),
        (OPERATEUR, "opérateur relecteur-synth (CLI)"),
        (
            Actor(
                canal="cli", authentifie=False, operateur="astreinte-1", urgence=True
            ),
            "opérateur astreinte-1 (CLI, accès d'urgence)",
        ),
        (
            Actor(canal="locale", authentifie=False),
            "interface locale, non authentifiée",
        ),
    ],
)
def test_acteur_lisible_par_son_pseudonyme_ou_son_operateur(actor, label):
    assert (
        actor_label(None if actor is None else actor.model_dump(mode="json")) == label
    )


def test_auteur_d_une_decision_relecteur_nomme_en_v1_acteur_en_v2():
    v1 = {"decision": "GO", "reviewer": "Relecteur de test", "reason": "revu"}
    assert human_label(v1) == "Relecteur de test"
    assert human_label(answer(acteur=OPERATEUR)) == "opérateur relecteur-synth (CLI)"


def test_dossier_tranche_montre_le_pseudonyme_du_relecteur_jamais_son_nom():
    web = interface()
    analyse(web, "analyste")
    decide(web, "relecteur")
    page = web.get("/contrats/c-attente", headers=bearer("analyste")).text
    assert f"utilisateur {RELECTEUR.subject} (interface authentifiée)" in page
    assert "relecteur.affiche" not in page


# --- premier contrôle des quatre yeux : ce qu'il laisse au graphe ------------------------------


def test_reponse_mal_formee_laissee_a_la_politique_du_graphe():
    """Le service ne tranche pas sur une réponse invalide : le graphe la refuse et la
    redemande, rien n'est scellé."""
    service = memory_service()
    service.analyse(PENDING_TEXT, contract_id="c-attente", actor=ACTEUR_ANALYSTE)
    malformed = answer() | {"acteur": {"canal": "inconnu"}}
    assert service.decide("c-attente", malformed)["statut"] == "suspendu"
    assert service.audit_store().entries() == []


def test_decision_systeme_hors_quatre_yeux_toujours_no_go():
    """Une décision système (expiration) n'est soumise aux quatre yeux ni au service ni
    dans le graphe ; elle ne peut être qu'un NO_GO. Aucune porte n'envoie de source."""
    service = memory_service()
    service.analyse(PENDING_TEXT, contract_id="c-attente", actor=ACTEUR_ANALYSTE)
    go = answer("GO", acteur=ACTEUR_ANALYSTE) | {"source": "systeme"}
    assert service.decide("c-attente", go)["statut"] == "suspendu"  # refusée
    no_go = answer("NO_GO", acteur=ACTEUR_ANALYSTE) | {"source": "systeme"}
    status = service.decide("c-attente", no_go)
    assert (status["final_decision"], status["human"]["source"]) == ("NO_GO", "systeme")
