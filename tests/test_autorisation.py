"""Autorisation (PR D2, ADR 005) : règles pures du domaine.

- Acteur : qui agit, par quel canal ; jamais de nom ni d'e-mail. Interface authentifiée
  (iss, sub du jeton vérifié) ; interface locale et CLI, non authentifiées ; l'accès
  d'urgence par la CLI est marqué.
- Rôles tirés des groupes du jeton vérifié, par une correspondance réglable.
- Quatre yeux : la revue est refusée à qui a lancé l'analyse, et à tout autre canal que
  celui de l'analyse, sauf en accès d'urgence (tracé).
- Second facteur du relecteur, réglable : amr ou acr acceptés.
"""

import pytest
from pydantic import ValidationError

from cdg.domain import authorization as authz
from cdg.domain.authorization import Actor
from cdg.domain.identity import Identity

ISS = "https://idp.example.org"
ANALYSTE = Actor(canal="interface", authentifie=True, iss=ISS, sub="sub-analyste")
RELECTEUR = Actor(canal="interface", authentifie=True, iss=ISS, sub="sub-relecteur")
LOCALE = Actor(canal="locale", authentifie=False)
OPERATEUR = Actor(canal="cli", authentifie=False, operateur="astreinte-1")
URGENCE = Actor(canal="cli", authentifie=False, operateur="astreinte-1", urgence=True)
MCP = Actor(canal="mcp", authentifie=False, operateur="assistant-poste-1")
MAPPING = {"analyste": ("cdg-analystes",), "relecteur": ("cdg-relecteurs",)}


def identity(groups=(), amr=(), acr=None) -> Identity:
    return Identity(ISS, "sub-x", tuple(groups), "X", amr=tuple(amr), acr=acr)


# --- acteur ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"canal": "interface", "authentifie": True, "iss": ISS},  # sans sub
        {"canal": "interface", "authentifie": False, "iss": ISS, "sub": "s"},
        {"canal": "locale", "authentifie": True},
        {"canal": "locale", "authentifie": False, "sub": "s"},
        {"canal": "locale", "authentifie": False, "operateur": "poste-1"},
        {"canal": "locale", "authentifie": False, "urgence": True},
        {"canal": "cli", "authentifie": False},  # sans opérateur
        {"canal": "cli", "authentifie": True, "operateur": "astreinte-1"},
        {"canal": "cli", "authentifie": False, "operateur": "camille@example.org"},
        {"canal": "cli", "authentifie": False, "operateur": "Camille Martin"},
        {"canal": "mcp", "authentifie": False},  # sans opérateur
        {"canal": "mcp", "authentifie": True, "operateur": "assistant-1"},
        {"canal": "mcp", "authentifie": False, "operateur": "assistant-1", "sub": "s"},
        {
            "canal": "mcp",
            "authentifie": False,
            "operateur": "assistant-1",
            "urgence": True,
        },
        {"canal": "mcp", "authentifie": False, "operateur": "Camille Martin"},
        {"canal": "mcp", "authentifie": False, "operateur": "camille@example.org"},
        {
            "canal": "interface",
            "authentifie": True,
            "iss": ISS,
            "sub": "s",
            "urgence": True,
        },
        {"canal": "interface", "authentifie": True, "iss": ISS, "sub": "s", "nom": "X"},
    ],
)
def test_acteur_incoherent_refuse(fields):
    with pytest.raises(ValidationError):
        Actor.model_validate(fields)


def test_acteur_sans_nom_ni_courriel():
    assert set(Actor.model_fields) == {
        "canal",
        "authentifie",
        "iss",
        "sub",
        "operateur",
        "urgence",
    }


def test_acteur_de_l_interface_tire_de_l_identite_verifiee():
    actor = authz.interface_actor(identity(["cdg-analystes"]))
    assert actor == Actor(canal="interface", authentifie=True, iss=ISS, sub="sub-x")


# --- rôles ---------------------------------------------------------------------------------


def test_roles_tires_des_groupes_par_correspondance():
    assert authz.roles(identity(["cdg-analystes"]), MAPPING) == {"analyste"}
    both = identity(["cdg-analystes", "cdg-relecteurs", "autre"])
    assert authz.roles(both, MAPPING) == {"analyste", "relecteur"}
    assert authz.roles(identity(["autre"]), MAPPING) == set()


def test_correspondance_des_roles_refusee_si_role_inconnu_ou_groupe_vide():
    with pytest.raises(authz.AuthorizationConfigError, match="administrateur"):
        authz.check_mapping({"administrateur": ("x",)})
    with pytest.raises(authz.AuthorizationConfigError, match="relecteur"):
        authz.check_mapping({"analyste": ("a",), "relecteur": ()})


@pytest.mark.parametrize(
    ("action", "analyste", "relecteur"),
    [
        ("lire", True, True),
        ("analyser", True, False),
        ("trancher", False, True),
        ("expirer", False, True),
    ],
)
def test_actions_permises_par_role(action, analyste, relecteur):
    assert authz.permitted({"analyste"}, action) is analyste
    assert authz.permitted({"relecteur"}, action) is relecteur
    assert authz.permitted(set(), action) is False


def test_role_requis_pour_une_action():
    assert authz.required_roles("trancher") == ("relecteur",)
    with pytest.raises(KeyError):
        authz.required_roles("supprimer")


# --- quatre yeux ---------------------------------------------------------------------------


def test_quatre_yeux_revue_par_une_autre_personne_admise():
    assert authz.four_eyes(ANALYSTE, RELECTEUR) is None


def test_quatre_yeux_revue_par_celui_qui_a_lance_l_analyse_refusee():
    same = Actor(canal="interface", authentifie=True, iss=ISS, sub="sub-analyste")
    assert "a lancé l'analyse" in authz.four_eyes(ANALYSTE, same)


def test_quatre_yeux_meme_sub_autre_emetteur_est_une_autre_personne():
    other = Actor(
        canal="interface",
        authentifie=True,
        iss="https://autre.example",
        sub=ANALYSTE.sub,
    )
    assert authz.four_eyes(ANALYSTE, other) is None


def test_contournement_par_changement_de_canal_refuse():
    """Analyse lancée dans l'interface, revue par une autre porte : refusée, sauf en accès
    d'urgence (tracé et scellé)."""
    analyse_cli = Actor(canal="cli", authentifie=False, operateur="lot-nocturne")
    assert "autre canal" in authz.four_eyes(analyse_cli, RELECTEUR)
    assert authz.four_eyes(ANALYSTE, URGENCE) is None


def test_quatre_yeux_analyse_par_mcp():
    """Analyse lancée par le serveur MCP (local, non authentifié) : revue refusée dans
    l'interface authentifiée (autre canal), sauf en accès d'urgence ; admise par les
    outils locaux, hors du cluster, comme pour la CLI."""
    assert "autre canal (mcp)" in authz.four_eyes(MCP, RELECTEUR)
    assert authz.four_eyes(MCP, URGENCE) is None
    assert authz.four_eyes(MCP, LOCALE) is None
    assert authz.four_eyes(MCP, OPERATEUR) is None


def test_acteur_mcp_non_authentifie_avec_operateur():
    assert MCP.model_dump() == {
        "canal": "mcp",
        "authentifie": False,
        "iss": None,
        "sub": None,
        "operateur": "assistant-poste-1",
        "urgence": False,
    }


def test_analyste_inconnu_revue_refusee_sauf_en_urgence():
    assert "analyste inconnu" in authz.four_eyes(None, RELECTEUR)
    assert authz.four_eyes(None, URGENCE) is None


def test_outils_locaux_non_authentifies_sans_quatre_yeux():
    """Interface du poste et CLI hors du cluster : aucune identité à comparer ; ils ne
    peuvent pas exister dans le cluster (démarrage refusé, rendu du chart refusé)."""
    assert authz.four_eyes(LOCALE, LOCALE) is None
    assert authz.four_eyes(ANALYSTE, OPERATEUR) is None


# --- second facteur ------------------------------------------------------------------------


def test_second_facteur_non_exige_par_defaut():
    policy = authz.SecondFactor()
    assert not policy.required
    assert authz.second_factor_proven(identity(), policy)


def test_second_facteur_prouve_par_amr_ou_acr():
    policy = authz.SecondFactor(
        amr=("mfa", "otp"), acr=("urn:mace:incommon:iap:silver",)
    )
    assert policy.required
    assert authz.second_factor_proven(identity(amr=["pwd", "otp"]), policy)
    assert authz.second_factor_proven(
        identity(acr="urn:mace:incommon:iap:silver"), policy
    )
    assert not authz.second_factor_proven(identity(amr=["pwd"]), policy)
    assert not authz.second_factor_proven(identity(acr="autre"), policy)
