"""Jeu de démonstration (J4) : 10 contrats synthétiques et 2 contrats piégés, plus un
contrat rédigé de façon réaliste (J5), du profil `fournisseur` ; 7 contrats du profil
`sous_traitance`, dont un rédigé de façon réaliste (PR 2 des profils).

- chaque citation attendue figure dans le texte masqué, et passe la vérification sous les
  termes du profil du contrat ;
- chaque contrat donne sa décision attendue dans le graphe (extraction et CRAG en
  doublure), sous son profil, revue humaine comprise, et est scellé ; `verify` valide
  toute la chaîne ;
- chaque règle du projet se déclenche dans au moins un contrat du jeu de son profil ;
- chaque constat bloquant ou pénalisant d'un contrat de sous-traitance a une référence en
  vigueur parmi les sources rattachées à sa clause ;
- pièges : paragraphe injecté (P1, critère 9 au T8), fausses pistes et données
  personnelles fictives (P2) ;
- contrats réalistes : formulations indirectes, informations dispersées, quantités non
  fixées ; en cas de doute, revue humaine plutôt qu'une décision automatique.
"""

import itertools
import json
from collections import Counter
from pathlib import Path

import pytest
import yaml
from demo_set import DEMO, EXPECTED, files, load
from doubles import (
    ABSENT,
    ACTEUR_ANALYSTE,
    CODE,
    FakeCrag,
    FixedExtractor,
    MemoryAuditStore,
    clauses_sous_traitance,
    make_deps,
    verdict,
)
from doubles import clauses as favorable
from langgraph.checkpoint.memory import InMemorySaver

from cdg import cli
from cdg.adapters.langgraph import orchestrator
from cdg.adapters.langgraph.checkpointer import strict_serializer
from cdg.application import demo_set, ingestion
from cdg.domain import audit, input_checks, masking, profiles, verification
from cdg.domain.config import load_config
from cdg.domain.decision import aggregate, decide
from cdg.domain.models import DOMAINS, Clause, category_required
from cdg.domain.numeric import rounded
from cdg.domain.profiles import Profile
from cdg.domain.rules import RULES

CONFIG = load_config()
FOURNISSEUR = profiles.spec("fournisseur")
ANALYSIS_DATE, CONTRACTS = load()
BY_ID = {c.id: c for c in CONTRACTS}
ANALYSED = [c for c in CONTRACTS if not c.rejected]
SOUS_TRAITANCE = [c for c in ANALYSED if c.profile == "sous_traitance"]
REALISTIC = BY_ID["demo-13-realiste-infogerance"]
REALISTIC_ST = "st-07-realiste-amenagement"  # contrat réaliste de la sous-traitance
# contrat favorable de chaque profil, sans constat : base des variantes des règles
FAVORABLE = {"fournisseur": favorable, "sous_traitance": clauses_sous_traitance}


def ids(contracts):
    return [c.id for c in contracts]


# --- Composition ---------------------------------------------------------------------------


def test_composition_du_jeu():
    assert len(CONTRACTS) == 20
    assert Counter(c.profile for c in CONTRACTS) == {
        "fournisseur": 13,
        "sous_traitance": 7,
    }
    assert set(files().values()) == {p.name for p in DEMO.glob("*.txt")}
    proposed = Counter(c.expected.get("proposed_decision", "rejet") for c in CONTRACTS)
    # GO dont un à marge faible et un piégé ; NO_GO dont le piégé à injection ;
    # ESCALADE dont les deux contrats réalistes
    assert proposed == {
        "GO": 5,
        "GO_RESERVES": 4,
        "NO_GO": 7,
        "ESCALADE": 3,
        "rejet": 1,
    }
    assert [c.id for c in CONTRACTS if c.injected or c.personal_data] == [
        "demo-11-piege-injection",
        "demo-12-piege-fausses-pistes",
    ]
    assert [c.id for c in CONTRACTS if c.realistic] == [REALISTIC.id, REALISTIC_ST]
    assert [c.id for c in CONTRACTS if c.human] == [
        "demo-03-go-logiciel",
        "demo-09-escalade-mobilier",
        "demo-11-piege-injection",  # tentative d'instruction : revue obligatoire
        "demo-13-realiste-infogerance",
        "st-07-realiste-amenagement",
    ]
    # les contrats de sous-traitance, dans l'ordre du tableau du plan (PR 2)
    assert ids(SOUS_TRAITANCE) == [
        "st-01-go-cabine",
        "st-02-reserves-marqueterie",
        "st-03-no-go-ferronnerie",
        "st-04-no-go-sellerie",
        "st-05-reserves-revetements",
        "st-06-no-go-ebenisterie",
        "st-07-realiste-amenagement",
    ]


# --- Profil de chaque contrat : champ obligatoire des attendus ------------------------------


def expected_without(tmp_path, change):
    """Copie des attendus, dont `change(contrats)` modifie les contrats."""
    data = yaml.safe_load(EXPECTED.read_text(encoding="utf-8"))
    change(data["contracts"])
    path = tmp_path / "attendus.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_chaque_contrat_du_jeu_declare_son_profil():
    data = yaml.safe_load(EXPECTED.read_text(encoding="utf-8"))
    assert [c["profile"] for c in data["contracts"].values()] == [
        *["fournisseur"] * 13,
        *["sous_traitance"] * 7,
    ]
    _, contracts = demo_set.load()
    assert {cid: c.profile for cid, c in contracts.items()} == {
        cid: c["profile"] for cid, c in data["contracts"].items()
    }


def test_contrat_du_jeu_sans_profil_refuse(tmp_path):
    path = expected_without(
        tmp_path,
        lambda contracts: contracts["demo-04-go-reserves-transport"].pop("profile"),
    )
    with pytest.raises(
        ValueError, match="profil absent du contrat demo-04.*attendus.yaml"
    ):
        demo_set.load(path)


@pytest.mark.parametrize("value", ["inexistant", "", None])
def test_contrat_du_jeu_de_profil_inconnu_refuse(tmp_path, value):
    path = expected_without(
        tmp_path,
        lambda contracts: contracts["demo-01-go-maintenance"].update(profile=value),
    )
    with pytest.raises(ValueError, match="profil inconnu"):
        demo_set.load(path)


# --- Citations attendues : dans le texte masqué, vérifiées ------------------------------------


@pytest.mark.parametrize("contract", ANALYSED, ids=ids(ANALYSED))
def test_citations_attendues_dans_le_texte_masque(contract):
    masked = masking.mask(contract.text, contract.parties).text
    assert input_checks.rejection(masked, ANALYSIS_DATE, CONFIG.input) is None
    # mêmes contrôles que verify_extraction : types, catégories et termes du profil du
    # contrat, citations exactes
    assert (
        verification.problems_of(
            masked,
            contract.clauses,
            profile=contract.profile,
            **CONFIG.terms(contract.profile)._asdict(),
            instruction_patterns=CONFIG.input.instruction_patterns,
        )
        == []
    )


def test_contrat_en_anglais_rejete_avant_extraction():
    contract = BY_ID["demo-10-rejet-anglais"]
    masked = masking.mask(contract.text, contract.parties).text
    reason = input_checks.rejection(masked, ANALYSIS_DATE, CONFIG.input)
    assert reason is not None
    assert reason.startswith(contract.expected["reject_reason"])


# --- Décision attendue dans le graphe, scellement, chaîne vérifiée --------------------------


def run(contract, store):
    extractor = FixedExtractor(contract.clauses)
    deps = make_deps(extractor, FakeCrag(), store)
    graph = orchestrator.build_graph(CONFIG, deps).compile(
        checkpointer=InMemorySaver(serde=strict_serializer())
    )
    status = orchestrator.run_contract(
        graph,
        contract.id,
        contract.text,
        contract.parties,
        analysis_date=ANALYSIS_DATE,
        config=CONFIG,
        actor=ACTEUR_ANALYSTE,
        code=CODE,
        profile=contract.profile,
    )
    if contract.human:
        # suspendu : rien de scellé pour ce contrat (le journal peut être partagé)
        assert status["statut"] == "suspendu", contract.id
        assert contract.id not in {e.contract_id for e in store.entries()}
        status = orchestrator.resume_thread(
            graph, contract.id, contract.human, config=CONFIG
        )
    return extractor, status


@pytest.mark.parametrize("contract", CONTRACTS, ids=ids(CONTRACTS))
def test_decision_attendue_dans_le_graphe(contract):
    store = MemoryAuditStore()
    extractor, status = run(contract, store)
    expected = contract.expected
    assert status["statut"] == "termine"
    if contract.rejected:
        assert status["reject_reason"].startswith(expected["reject_reason"])
        assert extractor.calls == [] and status["final_decision"] is None
    else:
        assert (status["proposed_decision"], status["final_decision"]) == (
            expected["proposed_decision"],
            expected["final_decision"],
        )
        # citations attendues vérifiées du premier coup, aucun échec de nœud
        assert len(extractor.calls) == 1 and status["failures"] == []
        assert status["explanation"]["decision"] == expected["final_decision"]
    [entry] = store.entries()
    assert entry.contract_id == contract.id
    assert entry.record["decision"]["final_decision"] == status["final_decision"]
    assert audit.record_profile(entry.record) == contract.profile


@pytest.mark.pg
def test_tout_le_jeu_scelle_puis_verify_valide_la_chaine(audit_journal, capsys):
    for contract in CONTRACTS:
        run(contract, audit_journal)
    entries = audit_journal.entries()
    assert [e.contract_id for e in entries] == ids(CONTRACTS)
    assert cli.main(["verify"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["verify"], out["enregistrements"], out["tete"]) == (
        "ok",
        20,
        entries[-1].chain_hash,
    )


# --- Chaque règle du projet se déclenche dans au moins un contrat du jeu ----------------------

# Délai de paiement : même règle, mêmes textes dans les deux profils (`rules._common`).
DELAI_DE_PAIEMENT = {
    "délai de paiement non stipulé (délai supplétif)": (
        "delai_paiement",
        "information",
        "non stipulé",
    ),
    "délai de paiement non chiffré": ("delai_paiement", "penalite", "non chiffré"),
    "délai au-delà du seuil, date de facture": (
        "delai_paiement",
        "penalite",
        "date de facture",
    ),
    "délai au-delà du seuil, fin de mois": (
        "delai_paiement",
        "penalite",
        "fin de mois",
    ),
    "délai au-delà du seuil, facture périodique": (
        "delai_paiement",
        "penalite",
        "facture périodique",
    ),
}

# Règles du projet, par profil puis par variante : (type de clause, effet, marqueur du
# texte du constat). Le balayage ci-dessous échoue si une règle produit un constat hors du
# catalogue de son profil.
RULE_CATALOGS: dict[Profile, dict[str, tuple[str, str, str]]] = {
    "fournisseur": {
        "responsabilité de l'acheteur illimitée": (
            "responsabilite_acheteur",
            "blocage",
            "illimitée",
        ),
        "plafond du fournisseur sous le minimum": (
            "responsabilite_fournisseur",
            "penalite",
            "sous le minimum",
        ),
        "révision de prix non plafonnée": (
            "revision_prix",
            "blocage",
            "non plafonnée",
        ),
        "pénalités d'exécution absentes": (
            "penalites_execution",
            "penalite",
            "absentes",
        ),
        "pénalités d'exécution sous le minimum": (
            "penalites_execution",
            "penalite",
            "sous le minimum",
        ),
        **DELAI_DE_PAIEMENT,
        "données personnelles sans accord de traitement": (
            "accord_traitement_donnees",
            "blocage",
            "sans accord",
        ),
        "transfert encadré par une garantie": (
            "transfert_hors_ue",
            "information",
            "encadré",
        ),
        "transfert sur clauses ad hoc, autorisation à vérifier": (
            "transfert_hors_ue",
            "penalite",
            "autorisation",
        ),
        "transfert sans garantie": ("transfert_hors_ue", "blocage", "sans garantie"),
        "localisation des données non précisée": (
            "transfert_hors_ue",
            "penalite",
            "localisation",
        ),
        "engagement au-delà du maximum": ("duree_engagement", "penalite", "au-delà"),
        "engagement non chiffré": ("duree_engagement", "penalite", "non chiffrée"),
        "préavis au-delà du maximum": ("preavis_resiliation", "penalite", "au-delà"),
        "préavis non chiffré": ("preavis_resiliation", "penalite", "non chiffré"),
    },
    # une entrée par cas du tableau des constats du profil (plan de la PR 2, tâche 6)
    "sous_traitance": {
        "responsabilité du donneur d'ordre illimitée": (
            "responsabilite_donneur_ordre",
            "blocage",
            "illimitée",
        ),
        "plafond du sous-traitant sous le minimum": (
            "responsabilite_sous_traitant",
            "penalite",
            "sous le minimum",
        ),
        "dessins protégés par la seule confidentialité": (
            "protection_design",
            "penalite",
            "seule confidentialité",
        ),
        "réutilisation des dessins autorisée": (
            "protection_design",
            "blocage",
            "réutilisation",
        ),
        "protection des dessins absente": ("protection_design", "penalite", "absente"),
        "révision de prix non plafonnée": (
            "revision_prix",
            "blocage",
            "non plafonnée",
        ),
        **DELAI_DE_PAIEMENT,
        "pénalités de retard d'exécution absentes": (
            "penalites_retard_execution",
            "penalite",
            "absentes",
        ),
        "pénalités de retard d'exécution sous le minimum": (
            "penalites_retard_execution",
            "penalite",
            "sous le minimum",
        ),
        "norme nommée sans preuves": (
            "obligation_certification",
            "penalite",
            "sans preuves",
        ),
        "conformité générale aux normes": (
            "obligation_certification",
            "penalite",
            "conformité générale",
        ),
        "obligation de certification absente": (
            "obligation_certification",
            "blocage",
            "absente",
        ),
        "assurance sous le minimum": (
            "assurance_sous_traitant",
            "penalite",
            "sous le minimum",
        ),
        "assurance non chiffrée": (
            "assurance_sous_traitant",
            "penalite",
            "non chiffrée",
        ),
        "assurance absente": ("assurance_sous_traitant", "penalite", "absente"),
        "délai d'exécution indicatif": ("delai_execution", "penalite", "indicatif"),
        "délai d'exécution absent": ("delai_execution", "penalite", "absent"),
    },
}

# Règles reprises d'une règle existante (`rules._common`) : le jeu du profil en déclenche au
# moins une entrée, leurs autres branches restant couvertes par le jeu du fournisseur ;
# chaque entrée des autres types se déclenche dans le jeu du profil (spec des profils,
# « Jeu de démonstration »).
REPRISES: dict[Profile, tuple[str, ...]] = {
    "fournisseur": (),
    "sous_traitance": (
        "responsabilite_donneur_ordre",
        "responsabilite_sous_traitant",
        "revision_prix",
        "delai_paiement",
    ),
}


def rules_of(finding, profile: Profile) -> list[str]:
    return [
        name
        for name, (kind, effect, marker) in RULE_CATALOGS[profile].items()
        if (finding.kind, finding.effect) == (kind, effect) and marker in finding.text
    ]


def findings_of(found, profile: Profile):
    return [f for d in DOMAINS for f in RULES[profile][d](found, CONFIG).findings]


def variants(profile: Profile):
    """Le contrat favorable du profil, chaque clause écartée à son tour : absente, puis
    chaque valeur d'essai (non chiffrée comprise) avec chaque catégorie admise pour son
    type. Une catégorie est toujours donnée quand la vérification de l'extraction
    l'exige."""
    spec, base = profiles.spec(profile), FAVORABLE[profile]
    values = [None, 0.0, 2.0, 5.0, 30.0, 45.0, 46.0, 60.0, 61.0, 90.0, 100.0, 150.0]
    for kind in spec.kinds:
        yield base(**{kind: ABSENT})
        for value, category in itertools.product(
            values, spec.kind_categories.get(kind, (None,))
        ):
            found = base(**{kind: value}, categories={kind: category})
            [clause] = [c for c in found if c.kind == kind]
            assert clause.category is not None or not category_required(
                clause, spec.category_kinds
            )
            yield found


# Constats du profil fournisseur, figés (tâche 5 de la PR 2 des profils) : le refactoring
# des règles communes ne change pas un caractère de leur texte. Le fichier a été produit
# avant ce refactoring, par le code du commit a96dce7 ; il ne se régénère pas pour faire
# passer un test : une différence est un défaut du code, sauf changement voulu d'un texte
# ou d'un réglage de la configuration.
CONSTATS_FIGES = Path(__file__).parent / "fixtures" / "constats-fournisseur.json"


def sans_point_de_depart():
    """Délais de paiement chiffrés sans point de départ connu : la vérification de
    l'extraction ne les laisse pas passer, mais la règle les lit au seuil le plus strict."""
    for value, category in itertools.product((30.0, 50.0, 90.0), (None, "inconnu")):
        yield favorable(delai_paiement=value, categories={"delai_paiement": category})


def constats_figes(found) -> dict:
    """Ce que le fichier figé garde d'une variante : ses écarts avec le contrat favorable
    (pour la lire) et les constats des quatre domaines, flottants arrondis."""
    reference = {c.kind: c for c in favorable()}
    return {
        "ecarts": [
            {
                "kind": c.kind,
                "present": c.present,
                "value": None if c.value is None else rounded(c.value),
                "category": c.category,
            }
            for c in found
            if c != reference[c.kind]
        ],
        "constats": [
            {
                "kind": f.kind,
                "effect": f.effect,
                "text": f.text,
                "penalty": None if f.penalty is None else rounded(f.penalty),
            }
            for f in findings_of(found, "fournisseur")
        ],
    }


def test_textes_des_constats_fournisseur_inchanges():
    figes = json.loads(CONSTATS_FIGES.read_text(encoding="utf-8"))["variantes"]
    courants = [
        constats_figes(found)
        for found in (*variants("fournisseur"), *sans_point_de_depart())
    ]
    assert len(courants) == len(figes)
    differences = [
        (i, fige, courant)
        for i, (fige, courant) in enumerate(zip(figes, courants, strict=True))
        if fige != courant
    ]
    assert not differences, differences[:3]


@pytest.mark.parametrize("profile", profiles.PROFILES)
def test_catalogue_des_regles_complet(profile):
    # chaque constat des règles relève d'une et une seule entrée du catalogue du profil,
    # et chaque entrée est atteinte : une règle ajoutée sans mise à jour du catalogue
    # échoue ici
    reached = Counter()
    for found in variants(profile):
        for finding in findings_of(found, profile):
            matched = rules_of(finding, profile)
            assert len(matched) == 1, (finding, matched)
            reached[matched[0]] += 1
    assert set(reached) == set(RULE_CATALOGS[profile])


@pytest.mark.parametrize("profile", profiles.PROFILES)
def test_chaque_regle_du_projet_se_declenche_dans_au_moins_un_contrat(profile):
    catalog = RULE_CATALOGS[profile]
    fired: dict[str, list[str]] = {name: [] for name in catalog}
    for contract in ANALYSED:
        if contract.profile != profile:
            continue
        for finding in findings_of(contract.clauses, profile):
            [name] = rules_of(finding, profile)
            fired[name].append(contract.id)
    reprises = REPRISES[profile]
    missing = [
        name
        for name, where in fired.items()
        if not where and catalog[name][0] not in reprises
    ]
    assert not missing, f"règles jamais déclenchées par le jeu : {missing}"
    silent = [
        kind
        for kind in reprises
        if not any(where for name, where in fired.items() if catalog[name][0] == kind)
    ]
    assert not silent, f"règles reprises jamais déclenchées par le jeu : {silent}"


def test_regles_reprises_de_la_sous_traitance():
    # les types repris sont ceux des règles communes ; les cinq autres sont propres au
    # profil, et chacune de leurs entrées doit se déclencher
    spec = profiles.spec("sous_traitance")
    assert set(REPRISES["sous_traitance"]) < set(spec.kinds)
    assert set(spec.kinds) - set(REPRISES["sous_traitance"]) == {
        "protection_design",
        "penalites_retard_execution",
        "obligation_certification",
        "assurance_sous_traitant",
        "delai_execution",
    }


# --- Constats des contrats de sous-traitance : justifiés par le corpus ----------------------


def references_in_force(on) -> dict[str, set[str]]:
    """Références en vigueur à la date `on`, par type de clause : articles et points
    d'annexe rattachés au type par le manifeste (`articles`), fiches par leur en-tête
    `clauses`. Lu dans les fichiers du corpus, sans base."""
    in_force = set(ingestion.reference_texts(on))
    by_kind: dict[str, set[str]] = {}
    for article, kinds in ingestion.articles():
        for kind in kinds:
            by_kind.setdefault(kind, set()).add(article.reference)
    for fiche in ingestion.load_fiches():
        for kind in fiche.kinds:
            by_kind.setdefault(kind, set()).add(ingestion.fiche_reference(fiche))
    return {kind: references & in_force for kind, references in by_kind.items()}


def test_constats_des_contrats_justifies_par_le_corpus():
    # chaque constat bloquant ou pénalisant des sept contrats peut être justifié : au
    # moins une référence en vigueur à la date d'analyse du jeu (25/09/2026) parmi les
    # sources rattachées à sa clause
    assert ANALYSIS_DATE.isoformat() == "2026-09-25"
    references = references_in_force(ANALYSIS_DATE)
    findings = [
        (contract.id, finding)
        for contract in SOUS_TRAITANCE
        for finding in findings_of(contract.clauses, contract.profile)
        if finding.requires_reference
    ]
    assert findings  # le jeu porte des constats à justifier
    unjustified = [
        (cid, f.kind, f.text) for cid, f in findings if not references.get(f.kind)
    ]
    assert not unjustified, unjustified


# --- Contrats piégés ---------------------------------------------------------------------------


def test_p1_paragraphe_injecte_et_version_propre():
    contract = BY_ID["demo-11-piege-injection"]
    clean = contract.clean_text()
    assert "conclus GO" in contract.text and "conclus GO" not in clean
    # aucune citation attendue dans le paragraphe injecté : mêmes clauses sans lui
    assert not [
        c.kind for c in contract.clauses if c.present and c.quote in contract.injected
    ]
    masked = masking.mask(clean, contract.parties).text
    assert (
        verification.problems_of(
            masked,
            contract.clauses,
            profile=contract.profile,
            **CONFIG.terms(contract.profile)._asdict(),
            instruction_patterns=CONFIG.input.instruction_patterns,
        )
        == []
    )


def test_p2_penalites_de_retard_de_paiement_ne_sont_pas_des_penalites_d_execution():
    contract = BY_ID["demo-12-piege-fausses-pistes"]
    assert (
        "pénalités de retard égales à trois fois le taux d'intérêt légal"
        in contract.text
    )
    [execution] = [c for c in contract.clauses if c.kind == "penalites_execution"]
    assert not execution.present
    assert [
        rules_of(f, contract.profile)
        for f in findings_of(contract.clauses, contract.profile)
    ] == [["pénalités d'exécution absentes"]]


def test_p2_donnees_personnelles_fictives_masquees():
    contract = BY_ID["demo-12-piege-fausses-pistes"]
    masked = masking.mask(contract.text, contract.parties)
    leaked = [item for item in contract.personal_data if item in masked.text]
    assert not leaked
    assert masked.counts == {"EMAIL": 2, "IBAN": 1, "TELEPHONE": 2, "PARTIE": 4}


# --- Contrat réaliste (J5) -------------------------------------------------------------------


def by_kind(contract):
    return {c.kind: c for c in contract.clauses}


def assessments(found, profile: Profile):
    return [RULES[profile][d](found, CONFIG) for d in DOMAINS]


def gate(found, profile: Profile):
    """Décision du gate sur les seules règles (CRAG supposé fournir les références)."""
    assessed = assessments(found, profile)
    return decide(
        [verdict(a.domain, score=a.score, hard_block=a.hard_block) for a in assessed],
        [],
        [],
        CONFIG,
    )


def test_contrat_realiste_formulations_indirectes():
    text, clauses = REALISTIC.text.casefold(), by_kind(REALISTIC)
    # pénalités d'exécution appelées « réfaction », jamais « pénalités »
    assert "pénalit" not in text
    assert clauses["penalites_execution"].present
    assert "réfaction" in clauses["penalites_execution"].quote
    # données personnelles décrites sans le terme juridique
    assert "données à caractère personnel" not in text
    assert "données personnelles" not in text
    assert clauses["donnees_personnelles"].present
    # quantités écrites comme dans un contrat réel : chiffre entre parenthèses, ou
    # nombre seulement en lettres
    delay, revision = clauses["delai_paiement"], clauses["revision_prix"]
    assert "quarante-cinq (45) jours fin de mois" in delay.quote
    assert delay.value == 45
    assert "quatre pour cent" in revision.quote and "4 %" not in revision.quote
    assert revision.value == 4
    # durée et préavis stipulés sans être fixés : présents, non chiffrés
    for kind, marker in (
        ("duree_engagement", "Planning directeur"),
        ("preavis_resiliation", "trimestre"),
    ):
        assert (clauses[kind].present, clauses[kind].value) == (True, None), kind
        assert marker in clauses[kind].quote, kind


def test_contrat_realiste_plafond_du_fournisseur_defini_dans_un_autre_article():
    supplier = by_kind(REALISTIC)["responsabilite_fournisseur"]
    definitions, _, rest = REALISTIC.text.partition("Article 2 -")
    assert supplier.quote in definitions and supplier.value == 120
    # l'article sur la responsabilité renvoie à la définition, sans chiffre
    assert "la responsabilité du Prestataire est limitée au Plafond" in rest


def test_contrat_realiste_escalade_par_prudence_sur_les_quantites_non_fixees():
    fired = sorted(
        name
        for f in findings_of(REALISTIC.clauses, REALISTIC.profile)
        for name in rules_of(f, REALISTIC.profile)
    )
    assert fired == ["engagement non chiffré", "préavis non chiffré"]
    scores = {
        a.domain: a.score for a in assessments(REALISTIC.clauses, REALISTIC.profile)
    }
    assert scores == {
        "juridique": 1.0,
        "financier": 1.0,
        "conformite": 1.0,
        "operationnel": 0.4,
    }
    # conflit entre domaines : aucune décision automatique, revue humaine
    outcome = gate(REALISTIC.clauses, REALISTIC.profile)
    assert (outcome.proposed, outcome.human_review, outcome.final) == (
        "ESCALADE",
        True,
        None,
    )


def test_contrat_realiste_une_seule_quantite_non_fixee_ne_suffit_pas_a_escalader():
    # limite assumée : avec une durée chiffrée, seul le préavis est pénalisé,
    # l'opérationnel reste à 0,7, sans conflit : GO automatique, constat visible
    fixed = [
        c.model_copy(update={"value": 24.0}) if c.kind == "duree_engagement" else c
        for c in REALISTIC.clauses
    ]
    assert [
        rules_of(f, REALISTIC.profile) for f in findings_of(fixed, REALISTIC.profile)
    ] == [["préavis non chiffré"]]
    outcome = gate(fixed, REALISTIC.profile)
    assert (outcome.proposed, outcome.human_review, outcome.final) == (
        "GO",
        False,
        "GO",
    )


@pytest.mark.parametrize("kind", FOURNISSEUR.kinds)
def test_contrat_realiste_chaque_clause_declaree_absente_est_redemandee(kind):
    # formulations indirectes (« réfaction », « annuaire des collaborateurs », « prix
    # sont indexés », « le Client ne répond que ») : une omission ne passe pas en silence
    masked = masking.mask(REALISTIC.text, REALISTIC.parties).text
    dropped = [
        Clause(kind=kind, present=False, quote="", value=None) if c.kind == kind else c
        for c in REALISTIC.clauses
    ]
    problems = verification.problems_of(
        masked,
        dropped,
        profile=REALISTIC.profile,
        **CONFIG.terms(REALISTIC.profile)._asdict(),
        instruction_patterns=CONFIG.input.instruction_patterns,
    )
    assert [p for p in problems if p.startswith("clause déclarée absente")] == [
        next(p for p in problems if p.endswith(f": {kind}"))
    ], problems


# --- Contrats de sous-traitance (PR 2 des profils) -------------------------------------------

# Constats, scores des quatre domaines (dans l'ordre de `DOMAINS`), agrégat et marge, puis
# décision proposée des sept contrats, sous les valeurs de démonstration du plan de la PR 2
# (« Décisions des sept contrats »). Un contrat bloqué n'a ni scores ni agrégat au tableau ;
# st-07 n'a pas d'agrégat (conflit entre domaines).
DECISIONS_SOUS_TRAITANCE = {
    "st-01-go-cabine": ([], (1.0, 1.0, 1.0, 1.0), (1.0, 0.25), "GO"),
    "st-02-reserves-marqueterie": (
        [
            "dessins protégés par la seule confidentialité",
            "pénalités de retard d'exécution sous le minimum",
            "assurance non chiffrée",
            "délai d'exécution indicatif",
        ],
        (0.7, 0.6, 0.7, 0.6),
        (0.655, 0.095),
        "GO_RESERVES",
    ),
    "st-03-no-go-ferronnerie": (
        ["réutilisation des dessins autorisée", "délai d'exécution absent"],
        None,
        None,
        "NO_GO",
    ),
    "st-04-no-go-sellerie": (
        ["protection des dessins absente", "obligation de certification absente"],
        None,
        None,
        "NO_GO",
    ),
    "st-05-reserves-revetements": (
        [
            "plafond du sous-traitant sous le minimum",
            "délai au-delà du seuil, date de facture",
            "norme nommée sans preuves",
            "assurance sous le minimum",
            "délai d'exécution indicatif",
        ],
        (0.5, 0.8, 0.4, 0.6),
        (0.57, 0.07),
        "GO_RESERVES",
    ),
    "st-06-no-go-ebenisterie": (
        [
            "responsabilité du donneur d'ordre illimitée",
            "révision de prix non plafonnée",
            "pénalités de retard d'exécution absentes",
        ],
        None,
        None,
        "NO_GO",
    ),
    "st-07-realiste-amenagement": (
        ["conformité générale aux normes", "assurance absente"],
        (1.0, 1.0, 0.4, 1.0),
        None,
        "ESCALADE",
    ),
}


def test_tableau_des_decisions_de_sous_traitance_complet():
    assert list(DECISIONS_SOUS_TRAITANCE) == ids(SOUS_TRAITANCE)


@pytest.mark.parametrize("cid", DECISIONS_SOUS_TRAITANCE)
def test_constats_scores_et_decision_des_contrats_de_sous_traitance(cid):
    contract = BY_ID[cid]
    rules, scores, total, decision = DECISIONS_SOUS_TRAITANCE[cid]
    assert contract.profile == "sous_traitance"
    # constats dans l'ordre des domaines, chacun d'une seule entrée du catalogue
    assert [
        name
        for f in findings_of(contract.clauses, contract.profile)
        for name in rules_of(f, contract.profile)
    ] == rules
    assessed = assessments(contract.clauses, contract.profile)
    if scores is None:
        assert any(a.hard_block for a in assessed)
    else:
        assert {a.domain: a.score for a in assessed} == dict(
            zip(DOMAINS, scores, strict=True)
        )
    if total is not None:
        verdicts = [verdict(a.domain, score=a.score) for a in assessed]
        computed = aggregate(verdicts, CONFIG)
        assert (computed.score, computed.margin) == total
    outcome = gate(contract.clauses, contract.profile)
    assert outcome.proposed == decision == contract.expected["proposed_decision"]
    # une ESCALADE va en revue humaine ; les autres sont automatiques
    assert outcome.human_review == (decision == "ESCALADE")
    assert (contract.human is not None) == outcome.human_review


def test_contrat_realiste_sous_traitance_formulations_indirectes():
    contract = BY_ID[REALISTIC_ST]
    text, clauses = contract.text, by_kind(contract)
    # conformité renvoyée aux normes en vigueur, justificatifs de la conformité aux normes
    # applicables tenus à disposition : le code lit une conformité générale, jamais des
    # preuves que le texte ne promet pas (chaque passage de l'article : test suivant)
    certification = clauses["obligation_certification"]
    assert "normes en vigueur" in certification.quote
    assert "à la disposition du Donneur d'ordre" in certification.quote
    assert "justificatifs de leur conformité aux normes applicables" in (
        certification.quote
    )
    assert certification.category == "conformite_generale"
    # quantités écrites comme dans un contrat réel : chiffre entre parenthèses, ou
    # nombre seulement en lettres
    delay, revision = clauses["delai_paiement"], clauses["revision_prix"]
    assert "quarante-cinq (45) jours fin de mois" in delay.quote
    assert delay.value == 45
    assert "trois pour cent" in revision.quote and "3 %" not in revision.quote
    assert revision.value == 3
    # plafond du sous-traitant défini à l'article des définitions ; l'article sur la
    # responsabilité y renvoie, sans chiffre
    cap = clauses["responsabilite_sous_traitant"]
    definitions, _, rest = text.partition("Article 2 -")
    assert cap.quote in definitions and cap.value == 150
    assert "dans la limite du Plafond de responsabilité du Sous-traitant" in rest
    # aucune assurance du sous-traitant : clause absente ; le « plan d'assurance qualité »
    # n'en est pas une (aucun terme d'absence de l'assurance au texte : vérifié par
    # `test_citations_attendues_dans_le_texte_masque`)
    assert not clauses["assurance_sous_traitant"].present
    assert "plan d'assurance qualité" in text


def article(text: str, number: int) -> list[str]:
    """Lignes d'un article du contrat, sans son titre."""
    _, _, rest = text.partition(f"\nArticle {number} - ")
    body, _, _ = rest.partition(f"\nArticle {number + 1} - ")
    return [line.strip() for line in body.splitlines()[1:] if line.strip()]


def test_contrat_realiste_sous_traitance_aucun_passage_de_la_qualite_lu_comme_preuves():
    # défense de st-07 : chaque phrase de l'article sur la qualité, et chaque membre de
    # phrase séparé par un point-virgule, évoque une catégorie pénalisée ; une extraction
    # qui en cite un en le classant « norme_et_preuves » (preuves remises, acceptée) est
    # contredite, jamais acceptée en silence (sinon : conformité à 0,7, écart 0,3, GO)
    contract = BY_ID[REALISTIC_ST]
    masked = masking.mask(contract.text, contract.parties).text
    lines = article(contract.text, 8)
    members = [m.strip() for line in lines for m in line.split(";") if m.strip()]
    passages = list(dict.fromkeys(lines + members))
    assert len(lines) >= 2 and len(passages) > len(lines)
    for passage in passages:
        claimed = [
            c.model_copy(update={"quote": passage, "category": "norme_et_preuves"})
            if c.kind == "obligation_certification"
            else c
            for c in contract.clauses
        ]
        problems = verification.problems_of(
            masked,
            claimed,
            profile=contract.profile,
            **CONFIG.terms(contract.profile)._asdict(),
            instruction_patterns=CONFIG.input.instruction_patterns,
        )
        # citation trouvée, catégorie contredite, rien d'autre
        assert len(problems) == 1, (passage, problems)
        assert problems[0].startswith("catégorie contredite"), (passage, problems)
        assert problems[0].endswith(": obligation_certification"), (passage, problems)


@pytest.mark.parametrize(
    "kind",
    [
        k
        for k in profiles.spec("sous_traitance").kinds
        if k != "assurance_sous_traitant"
    ],
)
def test_contrat_realiste_sous_traitance_chaque_clause_declaree_absente_est_redemandee(
    kind,
):
    # formulations indirectes (« ne répond que », plafond défini à part, « prix sont
    # indexés », renvoi au calendrier) : une omission ne passe pas en silence ; seule
    # l'assurance, vraiment absente, n'est évoquée nulle part
    contract = BY_ID[REALISTIC_ST]
    masked = masking.mask(contract.text, contract.parties).text
    dropped = [
        Clause(kind=kind, present=False, quote="", value=None) if c.kind == kind else c
        for c in contract.clauses
    ]
    problems = verification.problems_of(
        masked,
        dropped,
        profile=contract.profile,
        **CONFIG.terms(contract.profile)._asdict(),
        instruction_patterns=CONFIG.input.instruction_patterns,
    )
    assert [p for p in problems if p.startswith("clause déclarée absente")] == [
        next(p for p in problems if p.endswith(f": {kind}"))
    ], problems
