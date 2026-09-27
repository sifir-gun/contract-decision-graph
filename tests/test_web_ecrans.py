"""Interface web : écrans (décision du 27/09, points 11 à 16), client de test de FastAPI
sur le service des contrats en mémoire (vrai graphe, doublures du LLM et du CRAG)."""

import html
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from doubles import ABSENT, CONTRACT_TEXT, FixedExtractor, clauses
from web_helpers import (
    CONFIG,
    PENDING_TEXT,
    analyse,
    client,
    csrf,
    memory_service,
)

from cdg.adapters.web.presentation import DECISION_LABELS, STATE_LABELS
from cdg.application import demo_set


def text_of(page: str) -> str:
    """Texte visible approximatif : balises retirées, entités décodées, espaces réduits."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def decide(web, thread, **fields):
    data = {"decision": "GO_RESERVES", "relecteur": "Camille", "motif": "réserves"}
    token = csrf(web)  # le jeton tient au cookie, pas à la page
    return web.post(
        f"/contrats/{thread}/decision", data={"csrf": token, **data, **fields}
    )


# --- page commune ---------------------------------------------------------------------------


def test_page_en_francais_navigation_et_feuille_de_style_locale():
    page = client().get("/").text
    assert '<html lang="fr">' in page
    for link in (
        'href="/"',
        'href="/analyse"',
        'href="/journal"',
        'href="/administration"',
    ):
        assert link in page
    assert '<script src="/static/htmx.min.js"' in page
    assert '<link rel="stylesheet" href="/static/style.css"' in page
    assert "Démonstration" not in page  # bandeau du mode démo seulement


def test_page_inconnue():
    response = client().get("/absent")
    assert response.status_code == 404
    assert "Page introuvable" in response.text


# --- 11. liste des contrats -----------------------------------------------------------------


def test_liste_vide():
    assert "Aucun contrat" in client().get("/").text


def test_liste_etat_decision_et_filtre_en_attente():
    web = client()
    analyse(web, identifiant="c-go")
    analyse(web, PENDING_TEXT, identifiant="c-attente")
    page = web.get("/").text
    visible = text_of(page)
    assert 'href="/contrats/c-go"' in page and 'href="/contrats/c-attente"' in page
    assert STATE_LABELS["termine"] in visible
    assert STATE_LABELS["en_attente"] in visible
    assert DECISION_LABELS["GO"] in visible
    pending = web.get("/?attente=1").text
    assert 'href="/contrats/c-attente"' in pending
    assert 'href="/contrats/c-go"' not in pending


def test_liste_contrat_rejete():
    web = client()
    analyse(web, "Too short, in English, for any analysis.", identifiant="c-rejet")
    assert STATE_LABELS["rejete"] in text_of(web.get("/").text)


# --- 12. nouvelle analyse -------------------------------------------------------------------


def test_formulaire_d_analyse_etiquete():
    page = client().get("/analyse").text
    for field in ("contrat", "texte", "fichier", "parties", "date", "identifiant"):
        assert f'id="{field}"' in page and f'for="{field}"' in page
    _, contracts = demo_set.load()
    assert len(contracts) == 13
    for contract_id in contracts:
        assert f'value="{contract_id}"' in page
    assert "htmx-indicator" in page  # indicateur de chargement pendant l'analyse


def test_analyse_d_un_contrat_du_jeu_avec_ses_parties():
    service = memory_service()
    web = client(service)
    token = csrf(web)
    response = web.post(
        "/analyse",
        data={
            "csrf": token,
            "source": "jeu",
            "contrat": "demo-06-no-go-conseil",
            "parties": "Lambda Conseil Synthétique\nMu Énergie Synthétique",
            "identifiant": "demo-06",
        },
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/contrats/demo-06"
    raw = service.engine.values("demo-06")["raw_text"]
    assert "Lambda Conseil" not in raw and "Mu Énergie" not in raw


def test_analyse_identifiant_genere_et_date():
    service = memory_service()
    web = client(service)
    thread = analyse(web, identifiant="", date="2027-02-01")
    assert thread.startswith("contrat-")
    assert str(service.dossier(thread)["status"]["analysis_date"]) == "2027-02-01"


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"source": "texte", "texte": "  "}, "texte du contrat est vide"),
        ({"source": "jeu", "contrat": "inconnu"}, "contrat inconnu"),
        ({"source": "fichier"}, "aucun fichier"),
        ({"source": "autre"}, "source inconnue"),
        (
            {"source": "texte", "texte": CONTRACT_TEXT, "date": "31/12/2026"},
            "date invalide",
        ),
        (
            {"source": "texte", "texte": CONTRACT_TEXT, "identifiant": "a b/../c"},
            "identifiant invalide",
        ),
    ],
)
def test_analyse_entree_invalide(fields, message):
    web = client()
    token = csrf(web)
    response = web.post("/analyse", data={"csrf": token, **fields})
    assert response.status_code == 400
    assert message in response.text


# --- 13. dossier d'un contrat ---------------------------------------------------------------


def test_dossier_complet():
    service = memory_service(FixedExtractor(clauses(penalites_execution=ABSENT)))
    web = client(service)
    thread = analyse(web)
    page = web.get(f"/contrats/{thread}").text
    visible = text_of(page)
    dossier = service.dossier(thread)
    status = dossier["status"]
    # décision et statut en évidence, avec leur texte
    assert f'class="badge badge-{status["final_decision"].lower()}"' in page
    assert DECISION_LABELS[status["final_decision"]] in visible
    # explication : la phrase du parcours, puis un paragraphe par constat
    assert status["explanation"]["synthesis"] in visible
    for finding in status["explanation"]["findings"]:
        assert finding["text"] in visible
    # texte masqué, citations surlignées et reliées à leur clause
    assert "<mark>" in page
    for clause in dossier["clauses"]:
        assert f'id="clause-{clause["kind"]}"' in page
    assert 'href="#clause-revision_prix"' in page
    # verdict par domaine, références retenues avec leur texte ou leur absence
    for verdict in dossier["verdicts"]:
        assert f'id="domaine-{verdict["domain"]}"' in page
    assert "financier-ref-1" in visible and "absente du corpus" in visible
    # parcours, consommation, empreintes
    for step in dossier["parcours"]:
        assert step["created_at"] in page
    assert "extract_clauses" in visible
    for key in ("config_hash", "decision_hash", "chain_hash"):
        assert status[key] in page
    assert f'hx-get="/contrats/{thread}/rejeu"' in page


def test_dossier_references_du_corpus_avec_leur_texte():
    references = {
        "Fiche projet : Délais de paiement entre professionnels": {
            "source": "fiche",
            "texte": "Texte de la fiche <b>échappé</b>.",
        }
    }
    from cdg.adapters.web.presentation import reference_rows

    [row] = reference_rows(references)
    assert row == {
        "reference": "Fiche projet : Délais de paiement entre professionnels",
        "source": "fiche",
        "texte": "Texte de la fiche <b>échappé</b>.",
    }
    assert reference_rows({"x": None}) == [
        {"reference": "x", "source": None, "texte": None}
    ]


def test_dossier_alertes_tentative_d_instruction_et_extractions_refusees():
    wrong = [
        c.model_copy(update={"quote": "citation absente du contrat"})
        if c.kind == "revision_prix"
        else c
        for c in clauses()
    ]
    web = client(memory_service(FixedExtractor(wrong)))
    thread = analyse(web, PENDING_TEXT)
    visible = text_of(web.get(f"/contrats/{thread}").text)
    assert "Tentative d'instruction détectée" in visible
    assert "Extractions refusées" in visible and "revision_prix" in visible
    assert "Rapport d'échec" in visible


def test_dossier_inconnu():
    response = client().get("/contrats/absent")
    assert response.status_code == 404
    assert "thread inconnu" in response.text


def test_rejouer_un_contrat_scelle():
    web = client()
    thread = analyse(web)
    fragment = web.get(f"/contrats/{thread}/rejeu", headers={"hx-request": "true"})
    assert fragment.status_code == 200
    assert "<html" not in fragment.text  # fragment pour HTMX
    assert "Rejeu identique" in text_of(fragment.text)
    page = web.get(f"/contrats/{thread}/rejeu")
    assert "<html" in page.text  # sans JavaScript : page complète


def test_rejouer_un_contrat_non_scelle():
    web = client()
    thread = analyse(web, PENDING_TEXT)
    response = web.get(f"/contrats/{thread}/rejeu", headers={"hx-request": "true"})
    assert response.status_code == 409
    assert "aucun enregistrement scellé" in response.text


# --- 14. revue humaine ----------------------------------------------------------------------


def test_revue_humaine_demande_et_formulaire():
    web = client()
    thread = analyse(web, PENDING_TEXT)
    page = web.get(f"/contrats/{thread}").text
    visible = text_of(page)
    assert "Revue humaine" in visible and "Décision proposée" in visible
    for decision in CONFIG.human_policy.allowed_decisions:
        assert f'<option value="{decision}"' in page
    assert 'id="motif"' in page and "required" in page
    assert 'id="relecteur"' in page
    assert 'name="levee"' not in page  # aucun blocage dur : pas de levée proposée


def test_revue_humaine_acceptee():
    web = client()
    thread = analyse(web, PENDING_TEXT)
    response = decide(web, thread)
    assert response.status_code == 303
    page = web.get(f"/contrats/{thread}").text
    assert 'class="badge badge-go_reserves"' in page
    assert "Camille" in page
    assert 'id="revue"' not in page  # plus de formulaire


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"motif": " "}, "reason obligatoire"),
        ({"relecteur": "systeme:moi"}, "réservé aux décisions système"),
        ({"levee": "oui"}, "overrides_block sans blocage dur levé"),
    ],
)
def test_revue_refusee_redemandee_avec_le_motif(fields, message):
    web = client()
    thread = analyse(web, PENDING_TEXT)
    response = decide(web, thread, **fields)
    assert response.status_code == 422
    assert message in response.text
    assert 'id="revue"' in response.text  # le formulaire reste proposé


def test_levee_de_blocage_proposee_seulement_si_permise():
    policy = CONFIG.human_policy.model_copy(update={"hard_block_review": True})
    config = CONFIG.model_copy(update={"human_policy": policy})
    blocked = FixedExtractor(clauses(responsabilite_acheteur=None))
    web = client(memory_service(blocked, config=config))
    thread = analyse(web)
    page = web.get(f"/contrats/{thread}").text
    assert 'name="levee"' in page and "Lever le blocage dur" in page
    response = decide(web, thread, decision="GO", levee="oui", motif="garantie annexe")
    assert response.status_code == 303


def test_revue_d_un_contrat_termine_refusee():
    web = client()
    thread = analyse(web)
    response = decide(web, thread)
    assert response.status_code == 409
    assert "pas en attente" in response.text


# --- 15. journal d'audit --------------------------------------------------------------------


def test_journal_et_verification_de_la_chaine():
    web = client()
    analyse(web, identifiant="c-1")
    analyse(web, identifiant="c-2")
    page = web.get("/journal").text
    assert 'href="/contrats/c-1"' in page and 'href="/contrats/c-2"' in page
    assert 'hx-get="/journal/verification"' in page and 'id="tete"' in page
    ok = web.get("/journal/verification", headers={"hx-request": "true"})
    assert "Chaîne intègre" in text_of(ok.text) and "2 enregistrements" in ok.text
    head = re.search(r"[0-9a-f]{64}", ok.text).group(0)
    same = web.get("/journal/verification", params={"tete": head})
    assert "Chaîne intègre" in text_of(same.text)
    other = web.get("/journal/verification", params={"tete": "0" * 64})
    assert "Chaîne non conforme" in text_of(other.text)


def test_verification_tete_mal_formee():
    response = client().get("/journal/verification", params={"tete": "abc"})
    assert response.status_code == 400
    assert "empreinte invalide" in response.text


def test_journal_vide():
    assert "Aucun enregistrement scellé" in client().get("/journal").text


# --- 16. administration : expiration ------------------------------------------------------


def test_expiration_demande_une_confirmation():
    web = client()
    analyse(web, PENDING_TEXT, identifiant="c-attente")
    token = csrf(web, "/administration")
    response = web.post(
        "/administration/expiration", data={"csrf": token, "heures": "24"}
    )
    assert response.status_code == 200
    assert "Confirmer" in response.text and 'name="confirme"' in response.text
    # rien n'a expiré tant que la confirmation manque
    assert 'href="/contrats/c-attente"' in web.get("/?attente=1").text


def test_expiration_confirmee():
    service = memory_service()
    web = client(service)
    analyse(web, PENDING_TEXT, identifiant="c-attente")
    # les checkpoints sont datés à l'heure réelle : « plus tard » part d'elle
    later = client(replace(service, now=lambda: datetime.now(UTC) + timedelta(days=3)))
    token = csrf(later, "/administration")
    response = later.post(
        "/administration/expiration",
        data={"csrf": token, "heures": "24", "confirme": "oui"},
    )
    assert response.status_code == 200
    visible = text_of(response.text)
    assert "c-attente" in visible and DECISION_LABELS["NO_GO"] in visible
    assert 'href="/contrats/c-attente"' not in later.get("/?attente=1").text


@pytest.mark.parametrize("hours", ["0", "-3", "abc", ""])
def test_expiration_delai_invalide(hours):
    web = client()
    token = csrf(web, "/administration")
    response = web.post(
        "/administration/expiration", data={"csrf": token, "heures": hours}
    )
    assert response.status_code == 400
    assert "nombre d'heures" in text_of(response.text)
