"""Profils de contrat : types de clauses, partition par domaine, unités et catégories.

Le profil `fournisseur` reprend à l'identique les constantes qui étaient globales
(`models.REQUIRED_KINDS`, `DOMAIN_KINDS`, `KIND_CATEGORIES`, `verification.VALUE_UNITS`) :
ces tests figent l'existant (ADR 010).
"""

import ast
import dataclasses
from pathlib import Path
from typing import get_args

import pytest

from cdg.domain import profiles
from cdg.domain.models import (
    CERTIFICATION_REQUIREMENTS,
    DESIGN_PROTECTIONS,
    DOMAINS,
    EXECUTION_SCHEDULES,
    PAYMENT_BASES,
    TRANSFER_CATEGORIES,
    Clause,
    SubcontractCategory,
    category_required,
)

SRC = Path(__file__).resolve().parent.parent / "src"
FOURNISSEUR_KINDS = (
    "responsabilite_acheteur",
    "responsabilite_fournisseur",
    "revision_prix",
    "penalites_execution",
    "delai_paiement",
    "duree_engagement",
    "preavis_resiliation",
    "donnees_personnelles",
    "accord_traitement_donnees",
    "transfert_hors_ue",
)


def test_profils_connus():
    assert profiles.PROFILES == ("fournisseur",)
    assert profiles.LEGACY_PROFILE == "fournisseur"
    assert set(profiles.SPECS) == set(profiles.PROFILES)


def test_profil_fournisseur_reprend_l_existant():
    s = profiles.spec("fournisseur")
    assert s.kinds == FOURNISSEUR_KINDS
    assert s.domain_kinds == {
        "juridique": ("responsabilite_acheteur", "responsabilite_fournisseur"),
        "financier": ("revision_prix", "penalites_execution", "delai_paiement"),
        "conformite": (
            "donnees_personnelles",
            "accord_traitement_donnees",
            "transfert_hors_ue",
        ),
        "operationnel": ("duree_engagement", "preavis_resiliation"),
    }
    assert s.value_units == {
        "responsabilite_acheteur": "%",
        "responsabilite_fournisseur": "%",
        "revision_prix": "%",
        "penalites_execution": "%",
        "delai_paiement": "jours",
        "duree_engagement": "mois",
        "preavis_resiliation": "mois",
    }
    assert set(s.kind_categories) == {"delai_paiement", "transfert_hors_ue"}
    assert s.kind_categories["delai_paiement"] == PAYMENT_BASES
    assert s.kind_categories["delai_paiement"] == (
        "date_facture",
        "fin_de_mois",
        "facture_periodique",
    )
    assert s.kind_categories["transfert_hors_ue"] == TRANSFER_CATEGORIES
    assert s.category_kinds == frozenset({"transfert_hors_ue", "delai_paiement"})


@pytest.mark.parametrize("profile", profiles.PROFILES)
def test_chaque_type_dans_un_seul_domaine_pour_tous_les_profils(profile):
    s = profiles.spec(profile)
    assert set(s.domain_kinds) == set(DOMAINS)
    for domain, kinds in s.domain_kinds.items():
        assert all(profiles.KIND_DOMAIN[k] == domain for k in kinds)
    in_domains = [k for domain in DOMAINS for k in s.domain_kinds[domain]]
    assert sorted(in_domains) == sorted(s.kinds)  # partition : ni oubli, ni doublon


@pytest.mark.parametrize("profile", profiles.PROFILES)
def test_unites_et_categories_ne_portent_que_sur_des_types_du_profil(profile):
    s = profiles.spec(profile)
    assert set(s.value_units) <= set(s.kinds)
    assert set(s.kind_categories) <= set(s.kinds)


def test_profil_immuable():
    s = profiles.spec("fournisseur")
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.kinds = ()
    with pytest.raises(TypeError):
        s.value_units["delai_paiement"] = "mois"
    with pytest.raises(TypeError):
        s.domain_kinds["financier"] = ()
    with pytest.raises(TypeError):
        s.kind_categories["delai_paiement"] = ()


def test_tous_les_types_et_leur_domaine():
    assert profiles.ALL_KINDS == frozenset(FOURNISSEUR_KINDS)
    assert dict(profiles.KIND_DOMAIN) == {
        kind: domain
        for domain, kinds in profiles.spec("fournisseur").domain_kinds.items()
        for kind in kinds
    }
    for domain in DOMAINS:
        assert (
            profiles.kinds_of_domain(domain)
            == profiles.spec("fournisseur").domain_kinds[domain]
        )


def _spec(domain_kinds, *, units=None, categories=None):
    return profiles.ProfileSpec(
        kinds=tuple(k for kinds in domain_kinds.values() for k in kinds),
        domain_kinds=domain_kinds,
        value_units=units or {},
        kind_categories=categories or {},
        parties=profiles.Parties("a", "de a", "b", "de b"),
    )


def test_un_type_range_dans_deux_domaines_leve_une_erreur():
    first = _spec({"financier": ("delai_paiement", "revision_prix")})
    other_domain = _spec({"juridique": ("delai_paiement",)})
    with pytest.raises(ValueError, match="deux domaines.*delai_paiement"):
        profiles._kind_domain_table([first, other_domain])
    twice = _spec({"financier": ("revision_prix",), "juridique": ("revision_prix",)})
    with pytest.raises(ValueError, match="deux domaines.*revision_prix"):
        profiles._kind_domain_table([twice])


def test_un_type_commun_a_deux_profils_dans_le_meme_domaine_est_admis():
    first = _spec({"financier": ("revision_prix",)})
    second = _spec({"financier": ("revision_prix", "sous_traitance_x")})
    assert profiles._kind_domain_table([first, second]) == {
        "revision_prix": "financier",
        "sous_traitance_x": "financier",
    }


def test_types_d_un_domaine_union_des_profils_dans_l_ordre_de_declaration(monkeypatch):
    first = _spec({"financier": ("revision_prix", "delai_paiement")})
    second = _spec({"financier": ("x_nouveau", "revision_prix"), "juridique": ("y",)})
    monkeypatch.setattr(profiles, "PROFILES", ("fournisseur", "autre"))
    monkeypatch.setattr(profiles, "SPECS", {"fournisseur": first, "autre": second})
    assert profiles.kinds_of_domain("financier") == (
        "revision_prix",
        "delai_paiement",
        "x_nouveau",
    )
    assert profiles.kinds_of_domain("juridique") == ("y",)
    assert profiles.kinds_of_domain("conformite") == ()


@pytest.mark.parametrize(
    "value", ["sous_traitance", "Fournisseur", " fournisseur", "", None, 3, ["x"]]
)
def test_profil_inconnu_refuse(value):
    with pytest.raises(ValueError, match="profil inconnu") as error:
        profiles.parse(value)
    assert "profils : fournisseur" in str(error.value)


def test_profil_connu_accepte():
    assert profiles.parse("fournisseur") == "fournisseur"


def test_profil_inconnu_refuse_aussi_par_spec():
    with pytest.raises(ValueError, match="profil inconnu"):
        profiles.spec("sous_traitance")


def test_etat_sans_profil_lu_comme_fournisseur():
    assert profiles.of_state({}) == "fournisseur"
    assert profiles.of_state({"raw_text": "x"}) == "fournisseur"
    assert profiles.of_state({"profile": "fournisseur"}) == "fournisseur"


@pytest.mark.parametrize("value", ["autre", "", None])
def test_etat_avec_profil_inconnu_refuse(value):
    with pytest.raises(ValueError, match="profil inconnu"):
        profiles.of_state({"profile": value})


def test_la_categorie_est_exigee_selon_les_types_a_categorie():
    kinds = profiles.spec("fournisseur").category_kinds
    transfer = Clause(kind="transfert_hors_ue", present=True, quote="q", value=None)
    assert category_required(transfer, kinds)
    assert not category_required(transfer.model_copy(update={"present": False}), kinds)
    delay = Clause(kind="delai_paiement", present=True, quote="q", value=None)
    assert not category_required(delay, kinds)  # non chiffré : peut s'en passer
    assert category_required(delay.model_copy(update={"value": 30.0}), kinds)
    price = Clause(kind="revision_prix", present=True, quote="q", value=3.0)
    assert not category_required(price, kinds)


SOUS_TRAITANCE_KINDS = (
    "responsabilite_donneur_ordre",
    "responsabilite_sous_traitant",
    "protection_design",
    "revision_prix",
    "delai_paiement",
    "penalites_retard_execution",
    "obligation_certification",
    "assurance_sous_traitant",
    "delai_execution",
)


def test_categories_du_profil_sous_traitance():
    assert DESIGN_PROTECTIONS == (
        "usage_limite_au_projet",
        "confidentialite_seule",
        "reutilisation_autorisee",
    )
    assert CERTIFICATION_REQUIREMENTS == (
        "norme_et_preuves",
        "norme_sans_preuves",
        "conformite_generale",
    )
    assert EXECUTION_SCHEDULES == ("date_contractuelle", "delai_indicatif")
    # la catégorie portée par une clause du profil : une des quatre familles, sans doublon
    admitted = (
        DESIGN_PROTECTIONS
        + CERTIFICATION_REQUIREMENTS
        + EXECUTION_SCHEDULES
        + PAYMENT_BASES
    )
    assert get_args(SubcontractCategory) == admitted
    assert len(set(admitted)) == len(admitted)


def test_profil_sous_traitance_constantes():
    st = profiles.SOUS_TRAITANCE
    assert st.kinds == SOUS_TRAITANCE_KINDS
    assert dict(st.domain_kinds) == {
        "juridique": (
            "responsabilite_donneur_ordre",
            "responsabilite_sous_traitant",
            "protection_design",
        ),
        "financier": ("revision_prix", "delai_paiement", "penalites_retard_execution"),
        "conformite": ("obligation_certification", "assurance_sous_traitant"),
        "operationnel": ("delai_execution",),
    }
    assert dict(st.value_units) == {
        "responsabilite_donneur_ordre": "%",
        "responsabilite_sous_traitant": "%",
        "revision_prix": "%",
        "delai_paiement": "jours",
        "penalites_retard_execution": "%",
        "assurance_sous_traitant": "€",
    }
    assert set(st.kind_categories) == {
        "protection_design",
        "obligation_certification",
        "delai_execution",
        "delai_paiement",
    }
    assert st.kind_categories["protection_design"] == DESIGN_PROTECTIONS
    assert st.kind_categories["obligation_certification"] == CERTIFICATION_REQUIREMENTS
    assert st.kind_categories["delai_execution"] == EXECUTION_SCHEDULES
    assert st.kind_categories["delai_paiement"] == PAYMENT_BASES
    assert st.parties == profiles.Parties(
        "donneur d'ordre", "du donneur d'ordre", "sous-traitant", "du sous-traitant"
    )


def test_profil_sous_traitance_partitionne_ses_types():
    st = profiles.SOUS_TRAITANCE
    assert set(st.domain_kinds) == set(DOMAINS)
    in_domains = [k for domain in DOMAINS for k in st.domain_kinds[domain]]
    assert sorted(in_domains) == sorted(st.kinds)  # partition : ni oubli, ni doublon
    assert set(st.value_units) <= set(st.kinds)
    assert set(st.kind_categories) <= set(st.kinds)
    assert st.category_kinds == frozenset(st.kind_categories)


def test_profil_sous_traitance_immuable():
    st = profiles.SOUS_TRAITANCE
    with pytest.raises(dataclasses.FrozenInstanceError):
        st.parties = profiles.spec("fournisseur").parties
    with pytest.raises(TypeError):
        st.value_units["delai_paiement"] = "mois"
    with pytest.raises(TypeError):
        st.kind_categories["protection_design"] = ()


def test_types_communs_dans_le_meme_domaine_pour_les_deux_profils():
    table = profiles._kind_domain_table(
        [profiles.SPECS["fournisseur"], profiles.SOUS_TRAITANCE]
    )
    assert table["revision_prix"] == table["delai_paiement"] == "financier"
    assert table["protection_design"] == "juridique"
    assert table["delai_execution"] == "operationnel"


def test_parties_du_fournisseur():
    parties = profiles.spec("fournisseur").parties
    assert parties == profiles.Parties(
        "acheteur", "de l'acheteur", "fournisseur", "du fournisseur"
    )
    assert parties.client_of == "de l'acheteur"


def test_parties_gelees():
    parties = profiles.Parties("a", "de a", "b", "de b")
    assert (parties.client, parties.client_of) == ("a", "de a")
    assert (parties.contractor, parties.contractor_of) == ("b", "de b")
    with pytest.raises(dataclasses.FrozenInstanceError):
        parties.client = "autre"


# --- Architecture ---------------------------------------------------------------------


def test_profils_n_importent_que_les_modeles():
    tree = ast.parse((SRC / "cdg/domain/profiles.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names if a.name.startswith("cdg")}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "import relatif interdit"
            if node.module and node.module.startswith("cdg"):
                imported.add(node.module)
    # ni domain.config ni domain.rules : pas de cycle config -> profils -> règles -> config
    assert imported == {"cdg.domain.models"}


def test_plus_aucune_constante_globale_de_types():
    names = [
        "REQUIRED_KINDS",
        "DOMAIN_KINDS",
        "VALUE_UNITS",
        "KIND_CATEGORIES",
        "CATEGORY_KINDS",
    ]
    offenders = [
        f"{path.relative_to(SRC)} : {name}"
        for path in sorted(SRC.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
        for name in names
        if name.encode() in path.read_bytes()
    ]
    assert offenders == []
