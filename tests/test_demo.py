"""Jeu de démonstration (J4) : 10 contrats synthétiques et 2 contrats piégés, plus un
contrat rédigé de façon réaliste (J5).

- chaque citation attendue figure dans le texte masqué, et passe la vérification ;
- chaque contrat donne sa décision attendue dans le graphe (extraction et CRAG en
  doublure), revue humaine comprise, et est scellé ; `verify` valide toute la chaîne ;
- chaque règle du projet se déclenche dans au moins un contrat du jeu ;
- pièges : paragraphe injecté (P1, critère 9 au T8), fausses pistes et données
  personnelles fictives (P2) ;
- contrat réaliste : formulations indirectes, informations dispersées, quantités non
  fixées ; en cas de doute, revue humaine plutôt qu'une décision automatique.
"""

import itertools
import json
from collections import Counter

import pytest
from demo_set import DEMO, files, load
from doubles import (
    ABSENT,
    FakeCrag,
    FixedExtractor,
    MemoryAuditStore,
    make_deps,
    verdict,
)
from doubles import clauses as favorable
from langgraph.checkpoint.memory import InMemorySaver

from cdg import cli
from cdg.adapters.langgraph import orchestrator
from cdg.adapters.langgraph.checkpointer import strict_serializer
from cdg.domain import input_checks, masking, verification
from cdg.domain.config import load_config
from cdg.domain.decision import decide
from cdg.domain.models import (
    DOMAINS,
    KIND_CATEGORIES,
    REQUIRED_KINDS,
    category_required,
)
from cdg.domain.rules import RULES

CONFIG = load_config()
ANALYSIS_DATE, CONTRACTS = load()
BY_ID = {c.id: c for c in CONTRACTS}
ANALYSED = [c for c in CONTRACTS if not c.rejected]
REALISTIC = BY_ID["demo-13-realiste-infogerance"]


def ids(contracts):
    return [c.id for c in contracts]


# --- Composition ---------------------------------------------------------------------------


def test_composition_du_jeu():
    assert len(CONTRACTS) == 13
    assert set(files().values()) == {p.name for p in DEMO.glob("*.txt")}
    proposed = Counter(c.expected.get("proposed_decision", "rejet") for c in CONTRACTS)
    # GO dont un à marge faible et un piégé ; NO_GO dont le piégé à injection ;
    # ESCALADE dont le contrat réaliste
    assert proposed == {
        "GO": 4,
        "GO_RESERVES": 2,
        "NO_GO": 4,
        "ESCALADE": 2,
        "rejet": 1,
    }
    assert [c.id for c in CONTRACTS if c.injected or c.personal_data] == [
        "demo-11-piege-injection",
        "demo-12-piege-fausses-pistes",
    ]
    assert [c.id for c in CONTRACTS if c.realistic] == [REALISTIC.id]
    assert [c.id for c in CONTRACTS if c.human] == [
        "demo-03-go-logiciel",
        "demo-09-escalade-mobilier",
        "demo-11-piege-injection",  # tentative d'instruction : revue obligatoire
        "demo-13-realiste-infogerance",
    ]


# --- Citations attendues : dans le texte masqué, vérifiées ------------------------------------


@pytest.mark.parametrize("contract", ANALYSED, ids=ids(ANALYSED))
def test_citations_attendues_dans_le_texte_masque(contract):
    masked = masking.mask(contract.text, contract.parties).text
    assert input_checks.rejection(masked, ANALYSIS_DATE, CONFIG.input) is None
    # mêmes contrôles que verify_extraction : 10 types, catégories, citations exactes
    assert (
        verification.problems_of(
            masked,
            contract.clauses,
            absence_terms=CONFIG.extraction.absence_terms,
            category_terms=CONFIG.extraction.category_terms,
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
        13,
        entries[-1].chain_hash,
    )


# --- Chaque règle du projet se déclenche dans au moins un contrat du jeu ----------------------

# Règles du projet, par variante : (type de clause, effet, marqueur du texte du constat).
# Le balayage ci-dessous échoue si une règle produit un constat hors de ce catalogue.
RULE_CATALOG = {
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
    "révision de prix non plafonnée": ("revision_prix", "blocage", "non plafonnée"),
    "pénalités d'exécution absentes": ("penalites_execution", "penalite", "absentes"),
    "pénalités d'exécution sous le minimum": (
        "penalites_execution",
        "penalite",
        "sous le minimum",
    ),
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
}


def rules_of(finding) -> list[str]:
    return [
        name
        for name, (kind, effect, marker) in RULE_CATALOG.items()
        if (finding.kind, finding.effect) == (kind, effect) and marker in finding.text
    ]


def findings_of(found):
    return [f for d in DOMAINS for f in RULES[d](found, CONFIG).findings]


def variants():
    """Le contrat favorable, chaque clause écartée à son tour : absente, puis chaque valeur
    d'essai (non chiffrée comprise) avec chaque catégorie admise pour son type. Une
    catégorie est toujours donnée quand la vérification de l'extraction l'exige."""
    values = [None, 0.0, 2.0, 5.0, 30.0, 45.0, 46.0, 60.0, 61.0, 90.0, 100.0, 150.0]
    for kind in REQUIRED_KINDS:
        yield favorable(**{kind: ABSENT})
        for value, category in itertools.product(
            values, KIND_CATEGORIES.get(kind, (None,))
        ):
            found = favorable(**{kind: value}, categories={kind: category})
            [clause] = [c for c in found if c.kind == kind]
            assert clause.category is not None or not category_required(clause)
            yield found


def test_catalogue_des_regles_complet():
    # chaque constat des règles relève d'une et une seule entrée du catalogue, et chaque
    # entrée est atteinte : une règle ajoutée sans mise à jour du catalogue échoue ici
    reached = Counter()
    for found in variants():
        for finding in findings_of(found):
            matched = rules_of(finding)
            assert len(matched) == 1, (finding, matched)
            reached[matched[0]] += 1
    assert set(reached) == set(RULE_CATALOG)


def test_chaque_regle_du_projet_se_declenche_dans_au_moins_un_contrat():
    fired: dict[str, list[str]] = {name: [] for name in RULE_CATALOG}
    for contract in ANALYSED:
        for finding in findings_of(contract.clauses):
            [name] = rules_of(finding)
            fired[name].append(contract.id)
    missing = [name for name, where in fired.items() if not where]
    assert not missing, f"règles jamais déclenchées par le jeu : {missing}"


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
            absence_terms=CONFIG.extraction.absence_terms,
            category_terms=CONFIG.extraction.category_terms,
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
    assert [rules_of(f) for f in findings_of(contract.clauses)] == [
        ["pénalités d'exécution absentes"]
    ]


def test_p2_donnees_personnelles_fictives_masquees():
    contract = BY_ID["demo-12-piege-fausses-pistes"]
    masked = masking.mask(contract.text, contract.parties)
    leaked = [item for item in contract.personal_data if item in masked.text]
    assert not leaked
    assert masked.counts == {"EMAIL": 2, "IBAN": 1, "TELEPHONE": 2, "PARTIE": 4}


# --- Contrat réaliste (J5) -------------------------------------------------------------------


def by_kind(contract):
    return {c.kind: c for c in contract.clauses}


def gate(found):
    """Décision du gate sur les seules règles (CRAG supposé fournir les références)."""
    assessed = [RULES[d](found, CONFIG) for d in DOMAINS]
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
    fired = sorted(name for f in findings_of(REALISTIC.clauses) for name in rules_of(f))
    assert fired == ["engagement non chiffré", "préavis non chiffré"]
    scores = {d: RULES[d](REALISTIC.clauses, CONFIG).score for d in DOMAINS}
    assert scores == {
        "juridique": 1.0,
        "financier": 1.0,
        "conformite": 1.0,
        "operationnel": 0.4,
    }
    # conflit entre domaines : aucune décision automatique, revue humaine
    outcome = gate(REALISTIC.clauses)
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
    assert [rules_of(f) for f in findings_of(fixed)] == [["préavis non chiffré"]]
    outcome = gate(fixed)
    assert (outcome.proposed, outcome.human_review, outcome.final) == (
        "GO",
        False,
        "GO",
    )
