"""Explication (J4) : constats rattachés à leur clause, contrôles d'une explication, gabarit,
explicateur LLM et nœud `explain` ; critère 7 : une explication qui contredit le verdict est
rejetée.

Python pur et doublures : aucun appel réseau. Le graphe complet est testé dans
`test_explain_graph.py`.
"""

import pytest
from corpus_de_test import (
    CODE_A,
    CODE_A_REF,
    CODE_B,
    HOMONYM_NAMES,
    POINT_A,
    POINT_B,
    POINTS,
    point_source,
)
from doubles import (
    ABSENT,
    CONTRACT_TEXT,
    FaithfulExplainer,
    FakeLLM,
    clauses,
    faithful_explanation,
)

from cdg.application import explanation as application_explanation
from cdg.application import ingestion
from cdg.application.deps import TemplateOnly
from cdg.application.explanation import LLMExplainer
from cdg.application.nodes.explain import explain
from cdg.domain import explanation, profiles
from cdg.domain.config import load_config
from cdg.domain.explanation import Draft, ExplainedFinding
from cdg.domain.justification import justify
from cdg.domain.models import (
    ClauseRetrieval,
    HumanDecision,
    RetrievalTrace,
)
from cdg.domain.rules import RULES
from cdg.ports.llm import LLMOutputError, LLMTransientError

CONFIG = load_config()
# noms des sources du corpus du dépôt (citation et titre du manifeste), que l'orchestrateur
# passe au nœud explain
NAMES = ingestion.source_names()

# conformité : données personnelles sans accord (blocage, clause de l'accord) et transfert
# sur clauses ad hoc (pénalité, clause du transfert), chacune avec sa propre référence
CONFORMITE = clauses(
    accord_traitement_donnees=ABSENT,
    categories={"transfert_hors_ue": "clauses_contractuelles_ad_hoc"},
)
RETAINED = {
    "accord_traitement_donnees": ["RGPD, art. 28"],
    "transfert_hors_ue": ["RGPD, art. 46"],
}


def verdict(domain, found, retained, crag_findings=()):
    """Verdict réel : règles du domaine, puis justification sur un résumé du CRAG écrit à la
    main (références retenues par clause)."""
    trace = RetrievalTrace(
        clauses=[
            ClauseRetrieval(kind=k, queries=["q"], passes=1, retained=r, expired=[])
            for k, r in retained.items()
        ],
        findings=list(crag_findings),
    )
    return justify(RULES["fournisseur"][domain](found, CONFIG), trace)


def state(**overrides):
    """État à l'entrée d'explain : blocage dur de conformité, NO_GO final sans humain."""
    return {
        "verdicts": [verdict("conformite", CONFORMITE, RETAINED)],
        "proposed_decision": "NO_GO",
        "final_decision": "NO_GO",
        "clauses": CONFORMITE,
        "raw_text": CONTRACT_TEXT,
        **overrides,
    }


REQUEST = explanation.request(state())
ACCORD, TRANSFERT = REQUEST.findings


def entry(finding, text=None, references=None, kind="same"):
    return ExplainedFinding(
        id=finding.id,
        kind=finding.kind if kind == "same" else kind,
        references=finding.references if references is None else references,
        text=text or "Constat expliqué sans libellé de décision.",
    )


def draft(*entries) -> Draft:
    return Draft(findings=list(entries) or [entry(ACCORD), entry(TRANSFERT)])


# une explication qui contredit le verdict : un constat qui conclut à une autre décision
CONTRADICTORY = "Ce constat est sans gravité : les risques sont acceptables, GO."


def bad():
    return draft(entry(ACCORD, text=CONTRADICTORY), entry(TRANSFERT))


# --- Constats à expliquer ------------------------------------------------------------------


def test_un_constat_par_constat_du_verdict_rattache_a_sa_clause():
    assert [(f.id, f.domain, f.kind) for f in REQUEST.findings] == [
        ("conformite-1", "conformite", "accord_traitement_donnees"),
        ("conformite-2", "conformite", "transfert_hors_ue"),
    ]
    # seules références citables : celles retenues pour la clause du constat
    assert (ACCORD.references, TRANSFERT.references) == (
        ["RGPD, art. 28"],
        ["RGPD, art. 46"],
    )
    assert ACCORD.text.startswith("blocage : données personnelles traitées sans accord")


def test_constats_dans_l_ordre_des_domaines():
    financier = verdict("financier", clauses(penalites_execution=ABSENT), {})
    request = explanation.request(
        state(verdicts=[verdict("conformite", CONFORMITE, RETAINED), financier])
    )
    assert [f.id for f in request.findings] == [
        "financier-1",
        "financier-2",
        "conformite-1",
        "conformite-2",
    ]


def test_constat_du_crag_sans_clause_ni_reference():
    crag = "référence expirée à la date d'analyse : C. com., art. L441-10, non retenue"
    v = verdict(
        "financier", clauses(delai_paiement=90), {"delai_paiement": ["X"]}, [crag]
    )
    request = explanation.request(state(verdicts=[v]))
    assert [(f.kind, f.references) for f in request.findings] == [
        ("delai_paiement", ["X"]),
        (None, []),
    ]


def test_verdict_d_avant_le_j4_constats_non_rattaches():
    v = verdict("conformite", CONFORMITE, RETAINED).model_copy(
        update={"finding_kinds": []}
    )
    request = explanation.request(state(verdicts=[v]))
    assert [(f.kind, f.references) for f in request.findings] == [
        (None, []),
        (None, []),
    ]


def test_contexte_de_la_demande():
    human = HumanDecision(decision="GO", reviewer="r", reason="risque accepté")
    request = explanation.request(
        state(final_decision="GO", human=human, margin=0.04, failure_report=None)
    )
    assert (request.decision, request.margin, request.human, request.failure_stage) == (
        "GO",
        0.04,
        human,
        None,
    )
    stage = explanation.request(
        state(failure_report={"stage": "noeuds", "failures": []})
    )
    assert stage.failure_stage == "noeuds"


def test_explication_sans_decision_finale_erreur_explicite():
    with pytest.raises(ValueError, match="décision finale"):
        explanation.request(state(final_decision=None))


# --- Libellés de décision et articles cités --------------------------------------------------


@pytest.mark.parametrize(
    ("text", "labels"),
    [
        ("Décision : GO.", {"GO"}),
        ("Décision : GO_RESERVES.", {"GO_RESERVES"}),
        ("un GO sous réserves, ou GO avec réserves", {"GO_RESERVES"}),
        ("NO_GO, no go, No-Go", {"NO_GO"}),
        ("le dossier a été escaladé", {"ESCALADE"}),
        ("GO puis NO_GO", {"GO", "NO_GO"}),
        ("algorithme, Hugo, sous réserve de l'accord", set()),
    ],
)
def test_libelles_de_decision_nommes(text, labels):
    assert explanation.named_decisions(text) == labels


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("(art. 28 RGPD)", {"28"}),
        ("l'article L441-10 du code de commerce", {"L441-10"}),
        ("article L. 441-10", {"L441-10"}),
        ("art. 46, par. 3, a) RGPD", {"46"}),
        ("articles 44 à 46 et 1231-5", {"44", "46", "1231-5"}),
        ("les parties au contrat, 30 jours", set()),
        # points d'annexe : un numéro à points, qui garde ses points
        ("le point 4.2.10.2.1 de l'annexe", {"4.2.10.2.1"}),
        ("Règl. (UE) 748/2012, annexe I, point 21.A.307", {"21.A.307"}),
        ("au point 21.a.307.", {"21.A.307"}),
        ("points 4.2.10.2.1 et 21.A.307", {"4.2.10.2.1", "21.A.307"}),
        ("art. 83, par. 5, point c", {"83"}),
        ("le point de départ du délai, point 8", set()),
    ],
)
def test_articles_cites(text, found):
    assert explanation.articles(text) == found


def test_article_4_point_8_reste_l_article_4():
    assert explanation.articles("l'article 4, point 8 du RGPD") == {"4"}
    # acceptée aujourd'hui, elle le reste : l'art. 4 est retenu pour la clause
    finding = ACCORD.model_copy(update={"references": ["RGPD, art. 4"]})
    req = REQUEST.model_copy(update={"findings": [finding, TRANSFERT]})
    text = "Données personnelles au sens de l'article 4, point 8 du RGPD."
    assert (
        explanation.refusals(
            draft(entry(finding, text=text), entry(TRANSFERT)), req, names=NAMES
        )
        == []
    )


# --- Citations rattachées à leur source --------------------------------------------------------

CMF = "code-monetaire-financier"


@pytest.mark.parametrize(
    ("text", "found"),
    [
        # nom avant le numéro, sous la forme des références
        ("C. mon. fin., art. L112-2", {(CMF, "L112-2")}),
        ("RGPD art. 28", {("rgpd", "28")}),
        # nom après le numéro, casse ignorée, texte normalisé
        ("l'article L112-2 du code monétaire et financier", {(CMF, "L112-2")}),
        ("l’article L. 441-10 du Code de commerce", {("code-commerce", "L441-10")}),
        ("ART. 1231-5 DU CODE CIVIL", {("code-civil", "1231-5")}),
        ("(art. 28 RGPD)", {("rgpd", "28")}),
        # une énumération : seul le numéro que le nom touche lui est rattaché
        ("articles 44 à 46 du RGPD", {(None, "44"), ("rgpd", "46")}),
        # chaque citation a sa source ; le nom qui précède l'emporte sur celui qui suit
        (
            "RGPD, art. 28 ; C. civ., art. 1231-5",
            {("rgpd", "28"), ("code-civil", "1231-5")},
        ),
        (
            "C. com., art. L441-10 C. civ., art. 1231-5",
            {("code-commerce", "L441-10"), ("code-civil", "1231-5")},
        ),
        # un nom pris après une citation ne sert pas de nom avant à la suivante
        (
            "art. 1171 du Code civil, art. L442-1 du Code de commerce",
            {("code-civil", "1171"), ("code-commerce", "L442-1")},
        ),
        (
            "(art. 1171 C. civ., art. L442-1 C. com.)",
            {("code-civil", "1171"), ("code-commerce", "L442-1")},
        ),
        # une énumération : seul le numéro que le nom touche lui est rattaché, après
        # comme avant ; les autres, sans source, comparés par leur seul numéro
        (
            "articles 1171 et L442-1 du Code de commerce",
            {(None, "1171"), ("code-commerce", "L442-1")},
        ),
        (
            "C. civ., art. 1171 et L442-1",
            {("code-civil", "1171"), (None, "L442-1")},
        ),
        # homonymes du corpus : L112-2 au Code monétaire et financier, 1231-1 sans source
        (
            "les articles 1231-1 et L112-2 du code monétaire et financier",
            {(None, "1231-1"), (CMF, "L112-2")},
        ),
        # aucun nom ne touche la citation : aucune source
        ("article 28", {(None, "28")}),
        ("art. 46, par. 3, a) RGPD", {(None, "46")}),
        ("l'article 4, point 8 du RGPD", {(None, "4")}),
        ("le RGPD. Article 28 : sous-traitant", {(None, "28")}),
        ("art. 28 RGPDX", {(None, "28")}),
        # nom absent du corpus : ne rattache rien
        ("l'article L212-1 du code de la consommation", {(None, "L212-1")}),
    ],
)
def test_citations_rattachees_a_leur_source(text, found):
    assert explanation.citations(text, NAMES) == found


POINT_NAMES = {POINTS: (point_source()["citation"], point_source()["title"])}


@pytest.mark.parametrize(
    ("text", "found"),
    [
        (f"Règl. (UE) test, annexe, point {POINT_B}", {(POINTS, POINT_B)}),
        (f"règl. (UE) test, annexe I, point {POINT_A}", {(POINTS, POINT_A)}),
        (f"le point {POINT_A} de l'annexe du Règlement de test", {(POINTS, POINT_A)}),
        (f"le point {POINT_B} du règlement 748/2012", {(None, POINT_B)}),
    ],
)
def test_points_d_annexe_rattaches_a_leur_source(text, found):
    assert explanation.citations(text, POINT_NAMES) == found


def test_sans_noms_de_source_numeros_seuls():
    text = "C. mon. fin., art. L112-2 ; point 21.A.307"
    assert explanation.citations(text, {}) == {(None, "L112-2"), (None, "21.A.307")}
    assert explanation.articles(text) == {"L112-2", "21.A.307"}


@pytest.mark.parametrize(
    ("names", "message"),
    [
        (
            {"code-a": ("Code X",), "code-b": ("code  x",)},
            "nom de source commun à code-a et code-b",
        ),
        ({"code-a": ("Code A", " ")}, "code-a : nom de source vide"),
    ],
    ids=["commun", "vide"],
)
def test_noms_de_source_ambigus_ou_vides_refuses(names, message):
    with pytest.raises(ValueError, match=message):
        explanation.citations("art. 1", names)


def test_noms_de_source_en_chaine_seule_refuses():
    # une chaîne serait lue lettre par lettre : « RGPD » rattacherait « r », « g »…
    with pytest.raises(TypeError, match="code-a : noms de source attendus en liste"):
        explanation.citations("art. 1", {"code-a": "RGPD"})


def homonym_request():
    """Code A et code B, même numéro L1-1 : seul « Code A, art. L1-1 » est retenu pour
    le premier constat."""
    finding = ACCORD.model_copy(update={"references": [CODE_A_REF]})
    return finding, REQUEST.model_copy(update={"findings": [finding, TRANSFERT]})


def test_homonymes_compares_par_source():
    finding, req = homonym_request()

    def reasons(text, names=HOMONYM_NAMES):
        d = draft(entry(finding, text=text), entry(TRANSFERT))
        return explanation.refusals(d, req, names=names)

    refused = [
        (
            "conformite-1 : article cité hors des références retenues pour la clause "
            "accord_traitement_donnees : L1-1 (code-b)"
        )
    ]
    assert reasons("Voir l'article L1-1 du code B.") == refused
    assert reasons("Voir Ordonnance synthétique B, art. L1-1.") == refused
    # nom à voyelle initiale, après « de l' »
    assert reasons("Voir l'article L1-1 de l'Ordonnance synthétique B.") == refused
    assert reasons("Voir l'article L1-1 de l'Ordonnance synthétique A.") == []
    # le nom qui suit une citation n'est pas pris par la suivante
    text = "Voir art. 2 du code A, art. L1-1 du code B."
    assert explanation.citations(text, HOMONYM_NAMES) == {
        (CODE_A, "2"),
        (CODE_B, "L1-1"),
    }
    assert "L1-1 (code-b)" in reasons(text)[0]
    # la source retenue, ou aucun nom de source : acceptés
    assert reasons("Voir l'article L1-1 du code A.") == []
    assert reasons("Voir Code A, art. L1-1.") == []
    assert reasons("Voir l'article L1-1.") == []
    # sans les noms des sources, par le seul numéro, comme avant
    assert reasons("Voir l'article L1-1 du code B.", names={}) == []


# Explications acceptées aujourd'hui (par le seul numéro, sans nom de source) : celles des
# tests de ce fichier, celle de `faithful_explanation`, et des citations nommées justes.

FAITHFUL, _ = LLMExplainer(FakeLLM({"explain": faithful_explanation}))(
    REQUEST, [], profile="fournisseur"
)


def alone(finding, text):
    """Le seul constat `finding`, expliqué par `text`."""
    return draft(entry(finding, text=text)), REQUEST.model_copy(
        update={"findings": [finding]}
    )


def retained_alone(kind, references, domain="financier"):
    return explanation.FindingToExplain(
        id=f"{domain}-1",
        domain=domain,
        kind=kind,
        text="Constat des règles, sans article cité.",
        references=references,
    )


DELAI = retained_alone("delai_paiement", ["C. com., art. L441-10"])
REVISION = retained_alone("revision_prix", ["C. mon. fin., art. L112-2"])
PENALITES = retained_alone("penalites_execution", ["C. civ., art. 1231-5"])
RESPONSABILITE = retained_alone(
    "responsabilite_fournisseur",
    ["C. civ., art. 1170", "C. civ., art. 1231-3"],
    domain="juridique",
)
DEUX_CODES = retained_alone(
    "responsabilite_fournisseur",
    ["C. civ., art. 1171", "C. com., art. L442-1"],
    domain="juridique",
)
INDEXATION = retained_alone(
    "revision_prix", ["C. civ., art. 1231-1", "C. mon. fin., art. L112-2"]
)
POINT_RETENU = TRANSFERT.model_copy(
    update={"references": ["Règl. (UE) 748/2012, annexe I, point 21.A.307"]}
)
# transfert sans référence retenue : l'art. 46 n'est cité que par le texte du constat,
# sans nom qui le touche (« art. 46, par. 3, a) RGPD »)
TRANSFERT_SEUL = TRANSFERT.model_copy(update={"references": []})


@pytest.mark.parametrize(
    ("d", "req"),
    [
        pytest.param(draft(), REQUEST, id="sans-article"),
        pytest.param(
            explanation.template(REQUEST, reasons=[]).draft(), REQUEST, id="recopie"
        ),
        pytest.param(FAITHFUL, REQUEST, id="faithful_explanation"),
        pytest.param(
            *alone(
                ACCORD.model_copy(update={"references": ["RGPD, art. 4"]}),
                "Données personnelles au sens de l'article 4, point 8 du RGPD.",
            ),
            id="article-4-point-8",
        ),
        pytest.param(
            *alone(POINT_RETENU, "Le point 21.A.307 exige un certificat."),
            id="point-retenu",
        ),
        pytest.param(
            *alone(
                TRANSFERT,
                "Clauses ad hoc : autorisation à vérifier (art. 46, par. 3, a) RGPD).",
            ),
            id="article-du-constat",
        ),
        pytest.param(
            *alone(ACCORD, "Sans accord de traitement : l'article 28 du RGPD l'exige."),
            id="nom-apres",
        ),
        pytest.param(
            *alone(ACCORD, "Accord de traitement absent (RGPD, art. 28)."),
            id="nom-avant",
        ),
        pytest.param(
            *alone(TRANSFERT_SEUL, "L'article 46 du RGPD exige une autorisation."),
            id="article-du-constat-nomme",
        ),
        pytest.param(
            *alone(DELAI, "Délai au-delà de l'article L. 441-10 du Code de commerce."),
            id="code-de-commerce",
        ),
        pytest.param(
            *alone(DELAI, "Délai au-delà du plafond (C. com., art. L441-10, I)."),
            id="c-com",
        ),
        pytest.param(
            *alone(REVISION, "Indexation encadrée par l'article L112-2."),
            id="numero-seul",
        ),
        pytest.param(
            *alone(
                REVISION,
                "Indexation encadrée par l'article L112-2 du code monétaire et "
                "financier.",
            ),
            id="code-monetaire",
        ),
        pytest.param(
            *alone(PENALITES, "Clause pénale : art. 1231-5 du Code civil."),
            id="code-civil",
        ),
        pytest.param(
            *alone(RESPONSABILITE, "Plafond : articles 1170 et 1231-3 du Code civil."),
            id="deux-articles",
        ),
        pytest.param(
            *alone(
                DEUX_CODES, "Art. 1171 du Code civil, art. L442-1 du Code de commerce."
            ),
            id="deux-codes-nom-apres",
        ),
        pytest.param(
            *alone(
                DEUX_CODES,
                "Clause déséquilibrée (art. 1171 C. civ., art. L442-1 C. com.).",
            ),
            id="deux-codes-entre-parentheses",
        ),
        pytest.param(
            *alone(
                DEUX_CODES,
                "L'article 1171 du Code civil et l'article L442-1 du Code de commerce.",
            ),
            id="deux-codes-et",
        ),
        pytest.param(
            *alone(DEUX_CODES, "Voir C. civ., art. 1171 ; C. com., art. L442-1."),
            id="deux-codes-nom-avant",
        ),
        # acceptée sur main (7b4655b), vérifié le 21/10/2026
        pytest.param(
            *alone(DEUX_CODES, "Les articles 1171 et L442-1 du Code de commerce."),
            id="deux-codes-enumeration",
        ),
        pytest.param(
            *alone(
                INDEXATION,
                "Les articles 1231-1 et L112-2 du code monétaire et financier.",
            ),
            id="homonyme-dans-une-enumeration",
        ),
    ],
)
def test_explications_acceptees_aujourd_hui_le_restent(d, req):
    # acceptée aujourd'hui : par le seul numéro, aucune source n'étant nommée
    assert explanation.refusals(d, req, names={}) == []
    assert explanation.refusals(d, req, names=NAMES) == []


def test_enumeration_le_numero_touche_compare_par_source():
    # L112-2, que touche le nom du Code monétaire et financier, est refusé si seul son
    # homonyme du CPI est retenu ; 1231-1, sans source, comparé par son seul numéro
    finding = retained_alone(
        "revision_prix", ["C. civ., art. 1231-1", "CPI, art. L112-2"]
    )
    text = "Les articles 1231-1 et L112-2 du code monétaire et financier."
    assert explanation.refusals(*alone(finding, text), names=NAMES) == [
        (
            "financier-1 : article cité hors des références retenues pour la clause "
            "revision_prix : L112-2 (code-monetaire-financier)"
        )
    ]
    # limite : un numéro de l'énumération que le nom ne touche pas passe, même s'il vise
    # l'homonyme d'un autre code (ici L112-2 du CPI, quand seul celui du C. mon. fin.
    # est retenu), comme une citation sans nom
    finding = retained_alone(
        "revision_prix", ["C. mon. fin., art. L112-2", "CPI, art. L111-1"]
    )
    text = "Les articles L112-2 et L111-1 du CPI."
    assert explanation.refusals(*alone(finding, text), names=NAMES) == []


# --- Contrôles : critère 7 ---------------------------------------------------------------------


def test_explication_fidele_acceptee():
    assert explanation.refusals(draft(), REQUEST, names=NAMES) == []


def test_7_explication_qui_contredit_le_verdict_rejetee():
    assert explanation.refusals(bad(), REQUEST, names=NAMES) == [
        "conformite-1 : nomme une autre décision que la décision finale (NO_GO) : GO"
    ]


def test_7_autre_libelle_dans_un_constat_rejete():
    reasons = explanation.refusals(
        draft(
            entry(ACCORD, text="Ce point justifierait un GO_RESERVES."),
            entry(TRANSFERT),
        ),
        REQUEST,
        names=NAMES,
    )
    assert reasons == [
        (
            "conformite-1 : nomme une autre décision que la décision finale (NO_GO) : "
            "GO_RESERVES"
        )
    ]


def test_7_escalade_nommee_rejetee():
    text = "Constat relevé après escalade du dossier."
    reasons = explanation.refusals(
        draft(entry(ACCORD), entry(TRANSFERT, text=text)), REQUEST, names=NAMES
    )
    assert any("ESCALADE" in r for r in reasons)


def test_reference_non_retenue_rejetee():
    reasons = explanation.refusals(
        draft(entry(ACCORD, references=["C. civ., art. 1170"]), entry(TRANSFERT)),
        REQUEST,
        names=NAMES,
    )
    assert reasons == [
        (
            "conformite-1 : référence non retenue pour la clause accord_traitement_donnees : "
            "C. civ., art. 1170"
        )
    ]


def test_reference_d_une_autre_clause_rejetee():
    # art. 28 est retenu, mais pour l'accord de traitement, pas pour le transfert
    reasons = explanation.refusals(
        draft(entry(ACCORD), entry(TRANSFERT, references=["RGPD, art. 28"])),
        REQUEST,
        names=NAMES,
    )
    assert reasons == [
        (
            "conformite-2 : référence non retenue pour la clause transfert_hors_ue : "
            "RGPD, art. 28"
        )
    ]


def test_article_cite_dans_le_texte_hors_des_references_de_la_clause_rejete():
    text = "Le transfert relève de l'art. 28 RGPD."
    reasons = explanation.refusals(
        draft(entry(ACCORD), entry(TRANSFERT, text=text)), REQUEST, names=NAMES
    )
    # « art. 28 RGPD » nomme sa source : le motif la donne
    assert reasons == [
        (
            "conformite-2 : article cité hors des références retenues pour la clause "
            "transfert_hors_ue : 28 (rgpd)"
        )
    ]


def test_point_d_annexe_hors_references_refuse():
    text = "Le transfert relève du point 21.A.307 de l'annexe I du règlement 748/2012."
    reasons = explanation.refusals(
        draft(entry(ACCORD), entry(TRANSFERT, text=text)), REQUEST, names=NAMES
    )
    assert reasons == [
        (
            "conformite-2 : article cité hors des références retenues pour la clause "
            "transfert_hors_ue : 21.A.307"
        )
    ]


def test_point_d_annexe_retenu_citable():
    point = "Règl. (UE) 748/2012, annexe I, point 21.A.307"
    finding = TRANSFERT.model_copy(update={"references": [point]})
    req = REQUEST.model_copy(update={"findings": [ACCORD, finding]})
    text = "Le point 21.A.307 exige un certificat pour la pièce installée."
    assert (
        explanation.refusals(
            draft(entry(ACCORD), entry(finding, text=text)), req, names=NAMES
        )
        == []
    )


def test_article_du_texte_du_constat_citable():
    # le constat de transfert cite lui-même l'art. 46 (texte du code, non du LLM)
    text = "Clauses ad hoc : autorisation à vérifier (art. 46, par. 3, a) RGPD)."
    assert (
        explanation.refusals(
            draft(entry(ACCORD), entry(TRANSFERT, text=text)), REQUEST, names=NAMES
        )
        == []
    )


def test_constats_manquants_inconnus_ou_repetes_rejetes():
    stray = ExplainedFinding(id="juridique-9", kind=None, references=[], text="x")
    reasons = explanation.refusals(
        draft(entry(ACCORD), entry(ACCORD), stray), REQUEST, names=NAMES
    )
    assert reasons[:3] == [
        "constats non expliqués : conformite-2",
        "constats inconnus : juridique-9",
        "constats expliqués plusieurs fois : conformite-1",
    ]


def test_clause_erronee_ou_texte_vide_rejetes():
    reasons = explanation.refusals(
        draft(entry(ACCORD, kind="transfert_hors_ue"), entry(TRANSFERT, text=" ")),
        REQUEST,
        names=NAMES,
    )
    assert (
        "conformite-1 : clause transfert_hors_ue, attendue accord_traitement_donnees"
        in reasons
    )
    assert "conformite-2 : texte vide" in reasons


# --- Gabarit ---------------------------------------------------------------------------------


HUMAN = HumanDecision(
    decision="GO", reviewer="relecteur-synth", reason="risque accepté, pas un NO_GO"
)
SYSTEM = HumanDecision(
    decision="NO_GO",
    reviewer="systeme:expire",
    reason="timeout : 25 h",
    source="systeme",
)


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {
            "final_decision": "GO",
            "proposed_decision": "GO",
            "margin": 0.12,
            "verdicts": [],
        },
        {"final_decision": "GO", "human": HUMAN, "margin": 0.04},
        {"human": HUMAN.model_copy(update={"decision": "NO_GO"})},
        {"human": SYSTEM, "failure_report": {"stage": "noeuds", "failures": []}},
        {
            "final_decision": "GO_RESERVES",
            "human": HUMAN.model_copy(
                update={"decision": "GO_RESERVES", "overrides_block": True}
            ),
        },
    ],
)
def test_gabarit_passe_ses_propres_controles(overrides):
    request = explanation.request(state(**overrides))
    result = explanation.template(request, reasons=["motif"])
    assert (result.source, result.decision, result.attempts) == (
        "gabarit",
        request.decision,
        0,
    )
    assert explanation.refusals(result.draft(), request, names=NAMES) == []
    assert result.reasons == ["motif"]


def test_gabarit_un_constat_par_constat_avec_ses_references():
    result = explanation.template(REQUEST, reasons=[])
    assert [(f.id, f.kind, f.references, f.text) for f in result.findings] == [
        (f.id, f.kind, f.references, f.text) for f in REQUEST.findings
    ]
    assert result.synthesis.startswith("Décision finale : NO_GO.")


def test_gabarit_decision_systeme_et_revue_humaine_nommees_sans_autre_libelle():
    system = explanation.template(explanation.request(state(human=SYSTEM)), reasons=[])
    assert "délai de revue humaine est dépassé" in system.synthesis
    human = explanation.template(
        explanation.request(state(final_decision="GO", human=HUMAN)), reasons=[]
    )
    assert "revue humaine" in human.synthesis
    assert "NO_GO" not in human.synthesis and "risque accepté" not in human.synthesis


# --- Explicateur LLM : ce que le modèle reçoit -------------------------------------------------


def llm_answer(d: Draft):
    return d.model_dump()


def test_explicateur_recoit_le_verdict_fige_jamais_le_texte_du_contrat():
    llm = FakeLLM({"explain": llm_answer(draft())})
    result, usage = LLMExplainer(llm)(REQUEST, [], profile="fournisseur")
    [call] = llm.calls
    assert (call["tier"], call["node"], call["schema"]) == ("main", "explain", Draft)
    assert (result, usage.node) == (draft(), "explain")
    for finding in REQUEST.findings:
        assert finding.id in call["user"] and finding.text in call["user"]
    assert '"final_decision": "NO_GO"' in call["user"]
    # ni le texte du contrat, ni les citations des clauses : données non fiables
    prompt = call["system"] + call["user"]
    assert CONTRACT_TEXT not in prompt
    assert not [c.quote for c in CONFORMITE if c.quote and c.quote in prompt]


def test_explicateur_retour_de_refus_hors_des_donnees():
    llm = FakeLLM({"explain": llm_answer(draft())})
    LLMExplainer(llm)(
        REQUEST,
        ["essai 1 : conformite-1 : nomme une autre décision"],
        profile="fournisseur",
    )
    user = llm.calls[0]["user"]
    data, feedback = user.split("Essai précédent refusé", 1)
    assert "essai 1 : conformite-1 : nomme une autre décision" in feedback
    assert "essai 1" not in data


def test_explicateur_motif_humain_transmis_releveur_non():
    llm = FakeLLM({"explain": llm_answer(draft())})
    request = explanation.request(state(final_decision="GO", human=HUMAN))
    LLMExplainer(llm)(request, [], profile="fournisseur")
    user = llm.calls[0]["user"]
    assert "risque accepté, pas un NO_GO" in user and "relecteur-synth" not in user


# --- Explicateur LLM : prompt de chaque profil ---------------------------------------------------

PROMPTS = application_explanation.PROMPTS
RULES_HEADER = "Règles, sans exception :"


def prompt_named(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def rules_of(prompt: str) -> list[str]:
    """Lignes du bloc « Règles, sans exception », jusqu'à la première ligne vide."""
    lines = prompt.splitlines()
    start = lines.index(RULES_HEADER)
    end = lines.index("", start) if "" in lines[start:] else len(lines)
    return lines[start:end]


def test_prompt_du_profil_fournisseur_inchange():
    assert application_explanation.PROMPT_FILES["fournisseur"] == "explain_system.md"
    assert prompt_named("explain_system.md").startswith(
        "Tu expliques les constats d'une analyse de contrat fournisseur."
    )


def test_un_prompt_d_explication_pour_chaque_profil_et_seulement_eux():
    assert set(application_explanation.PROMPT_FILES) == set(profiles.PROFILES)
    for profile in profiles.PROFILES:
        assert (PROMPTS / application_explanation.PROMPT_FILES[profile]).is_file()


def test_explicateur_passe_le_prompt_du_profil():
    llm = FakeLLM({"explain": llm_answer(draft())})
    LLMExplainer(llm)(REQUEST, [], profile="fournisseur")
    [call] = llm.calls
    assert call["system"] == prompt_named("explain_system.md")


def test_explicateur_lit_le_prompt_du_profil_demande(monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES", ("fournisseur", "autre"))
    monkeypatch.setattr(
        application_explanation,
        "PROMPT_FILES",
        {"fournisseur": "explain_system.md", "autre": "crag_grade_system.md"},
    )
    llm = FakeLLM({"explain": llm_answer(draft())})
    explainer = LLMExplainer(llm)
    explainer(REQUEST, [], profile="autre")
    explainer(REQUEST, [], profile="fournisseur")
    autre, fournisseur = llm.calls
    assert autre["system"] == prompt_named("crag_grade_system.md")
    assert fournisseur["system"] == prompt_named("explain_system.md")


def test_explicateur_lit_les_prompts_de_tous_les_profils_a_la_construction(monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES", ("fournisseur", "autre"))
    monkeypatch.setattr(
        application_explanation,
        "PROMPT_FILES",
        {"fournisseur": "explain_system.md", "autre": "absent.md"},
    )
    with pytest.raises(FileNotFoundError, match="absent.md"):
        LLMExplainer(FakeLLM())


def test_explicateur_refuse_un_profil_inconnu_sans_appeler_le_llm():
    llm = FakeLLM({"explain": llm_answer(draft())})
    with pytest.raises(ValueError, match="profil inconnu"):
        LLMExplainer(llm)(REQUEST, [], profile="inexistant")
    assert llm.calls == []


def test_prompt_du_profil_sous_traitance_enregistre():
    files = application_explanation.PROMPT_FILES
    assert files["sous_traitance"] == "explain_sous_traitance_system.md"


def test_explicateur_passe_le_prompt_de_la_sous_traitance():
    llm = FakeLLM({"explain": llm_answer(draft())})
    LLMExplainer(llm)(REQUEST, [], profile="sous_traitance")
    [call] = llm.calls
    assert call["system"] == prompt_named("explain_sous_traitance_system.md")


# Prompts des deux profils, lus par leur entrée de `PROMPT_FILES` : mêmes règles, sujet et
# parties propres au profil.


def test_prompts_d_explication_memes_regles():
    files = application_explanation.PROMPT_FILES
    fournisseur = prompt_named(files["fournisseur"])
    sous_traitance = prompt_named(files["sous_traitance"])
    assert rules_of(sous_traitance) == rules_of(fournisseur)
    assert len(rules_of(fournisseur)) == 6  # l'en-tête et cinq règles
    # le sujet et les parties changent, pas les règles
    assert "contrat de sous-traitance" in sous_traitance
    assert "donneur d'ordre" in sous_traitance
    assert "contrat fournisseur" not in sous_traitance
    assert "contrat de sous-traitance" not in fournisseur


# --- Nœud explain --------------------------------------------------------------------------------


def never_retried(exc):
    return False


def run_explain(explainer, retrying=never_retried, **overrides):
    return explain(
        state(**overrides),
        explainer=explainer,
        decision_config=CONFIG,
        source_names=NAMES,
        retrying=retrying,
    )


def test_explication_llm_acceptee_au_premier_essai():
    llm = FakeLLM({"explain": [llm_answer(draft())]}, tokens=(120, 80))
    out = run_explain(LLMExplainer(llm))
    result = out["explanation"]
    assert (result.source, result.attempts, result.reasons) == ("llm", 1, [])
    assert result.draft() == draft()
    assert [(u.node, u.tokens_in) for u in out["usage"]] == [("explain", 120)]


def test_7_explication_contradictoire_regeneree_une_fois_puis_acceptee():
    llm = FakeLLM({"explain": [llm_answer(bad()), llm_answer(draft())]})
    out = run_explain(LLMExplainer(llm))
    result = out["explanation"]
    assert (result.source, result.attempts) == ("llm", 2)
    expected = (
        "essai 1 : conformite-1 : nomme une autre décision que la décision finale "
        "(NO_GO) : GO"
    )
    assert result.reasons == [expected]
    # le second essai reçoit les motifs du refus
    assert "nomme une autre décision" in llm.calls[1]["user"]
    assert len(out["usage"]) == 2


def test_7_deux_explications_refusees_puis_gabarit():
    llm = FakeLLM({"explain": [llm_answer(bad()), llm_answer(bad())]})
    out = run_explain(LLMExplainer(llm))
    result = out["explanation"]
    assert (result.source, result.attempts) == ("gabarit", 2)
    assert [r.split(" : ", 1)[0] for r in result.reasons] == ["essai 1", "essai 2"]
    assert len(llm.calls) == 2 and len(out["usage"]) == 2
    assert result.synthesis.startswith("Décision finale : NO_GO.")


def test_nombre_d_essais_lu_dans_la_configuration():
    config = CONFIG.model_copy(
        update={"explain": CONFIG.explain.model_copy(update={"max_attempts": 1})}
    )
    llm = FakeLLM({"explain": [llm_answer(bad())]})
    out = explain(
        state(),
        explainer=LLMExplainer(llm),
        decision_config=config,
        source_names=NAMES,
        retrying=never_retried,
    )
    assert (out["explanation"].source, len(llm.calls)) == ("gabarit", 1)


def test_explication_recoit_le_profil_de_l_etat():
    # état sans clé `profile` (d'avant les profils) : le seul profil possible alors
    without, with_profile = FaithfulExplainer(), FaithfulExplainer()
    run_explain(without)
    run_explain(with_profile, profile="fournisseur")
    assert without.profiles == with_profile.profiles == ["fournisseur"]


def test_explication_recoit_le_profil_par_mot_cle():
    seen = {}

    def explainer(request, feedback, *, profile):
        seen["profile"] = profile
        return FaithfulExplainer()(request, feedback, profile=profile)

    run_explain(explainer, profile="fournisseur")
    assert seen == {"profile": "fournisseur"}


def test_explication_refuse_un_profil_inconnu_sans_gabarit_de_repli():
    explainer = FaithfulExplainer()
    with pytest.raises(ValueError, match="profil inconnu"):
        run_explain(explainer, profile="inexistant")
    assert explainer.profiles == []


def test_noeud_explain_compare_par_les_noms_recus():
    # seul « Code A, art. L1-1 » est retenu pour l'accord ; l'explication cite le code B
    retained = {**RETAINED, "accord_traitement_donnees": [CODE_A_REF]}
    homonyms = state(verdicts=[verdict("conformite", CONFORMITE, retained)])

    def explainer(request, feedback, *, profile):
        faithful, spent = FaithfulExplainer()(request, feedback, profile=profile)
        first, *rest = faithful.findings
        wrong = first.model_copy(update={"text": "Voir l'article L1-1 du code B."})
        return Draft(findings=[wrong, *rest]), spent

    def run_with(names):
        return explain(
            homonyms,
            explainer=explainer,
            decision_config=CONFIG,
            source_names=names,
            retrying=never_retried,
        )["explanation"]

    refused = run_with(HOMONYM_NAMES)
    assert (refused.source, refused.reasons[0]) == (
        "gabarit",
        (
            "essai 1 : conformite-1 : article cité hors des références retenues pour "
            "la clause accord_traitement_donnees : L1-1 (code-b)"
        ),
    )
    # sans les noms des sources, par le seul numéro : acceptée
    assert run_with({}).source == "llm"


class Failing:
    def __init__(self, exc):
        self.exc, self.calls = exc, 0

    def __call__(self, request, feedback, *, profile):
        self.calls += 1
        raise self.exc


def test_erreur_du_llm_gabarit_avec_le_motif():
    failing = Failing(LLMOutputError("réponse non structurée"))
    out = run_explain(failing)
    result = out["explanation"]
    assert (result.source, result.attempts, failing.calls) == ("gabarit", 1, 1)
    assert result.reasons == ["essai 1 : LLMOutputError : réponse non structurée"]
    assert "usage" not in out


def test_erreur_passagere_relancee_tant_que_la_reprise_le_permet():
    failing = Failing(LLMTransientError("429"))
    with pytest.raises(LLMTransientError):
        run_explain(failing, retrying=lambda exc: True)
    # dernière tentative : plus de reprise, le gabarit prend le relais
    out = run_explain(failing, retrying=lambda exc: False)
    assert out["explanation"].reasons == ["essai 1 : LLMTransientError : 429"]


def test_sans_llm_gabarit_avec_le_motif_et_aucun_appel():
    out = run_explain(TemplateOnly("décision système (expire) : gabarit"))
    result = out["explanation"]
    assert (result.source, result.attempts, result.reasons) == (
        "gabarit",
        0,
        ["décision système (expire) : gabarit"],
    )
    assert set(out) == {"explanation"}


# --- Synthèse écrite par le code (série 4, contrat 09) ---------------------------------------

ESCALATED = HumanDecision(
    decision="GO_RESERVES", reviewer="relecteur-demo", reason="à fixer par avenant"
)


def test_synthese_du_parcours_ecrite_par_le_code_meme_si_le_llm_en_propose_une():
    # contrat 09 : les règles n'ont rien proposé (conflit), l'humain a tranché. En série 4,
    # le LLM a écrit que la proposition des règles et la revue humaine « convergent ».
    # Il n'écrit plus la synthèse : une synthèse qu'il ajouterait est ignorée.
    escalated = state(
        proposed_decision="ESCALADE", final_decision="GO_RESERVES", human=ESCALATED
    )
    answer = {
        **llm_answer(draft()),
        "synthesis": "La proposition des règles et la revue humaine convergent : GO.",
    }
    out = run_explain(LLMExplainer(FakeLLM({"explain": answer})), **escalated)
    result = out["explanation"]
    assert (result.source, result.reasons) == ("llm", [])
    assert result.synthesis == explanation.path(explanation.request(escalated))
    assert "convergent" not in result.synthesis
    assert result.synthesis.startswith(
        "Décision finale : GO_RESERVES. Les règles n'ont proposé aucune décision et ont "
        "demandé une revue humaine, qui a tranché."
    )


@pytest.mark.parametrize(
    ("overrides", "sentence"),
    [
        ({}, "Décision rendue par les règles du projet, sans revue humaine."),
        (
            {"final_decision": "GO", "proposed_decision": "GO", "human": HUMAN},
            "Décision proposée par les règles et confirmée en revue humaine.",
        ),
        (
            {"final_decision": "GO", "proposed_decision": "NO_GO", "human": HUMAN},
            "Décision prise en revue humaine, différente de la proposition des règles.",
        ),
        (
            {"input_findings": ["tentative d'instruction détectée : « x »"]},
            (
                "Tentative d'instruction détectée dans le contrat : revue humaine "
                "obligatoire."
            ),
        ),
    ],
)
def test_parcours_decrit_par_le_code(overrides, sentence):
    assert sentence in explanation.path(explanation.request(state(**overrides)))


def test_sans_constat_aucun_appel_au_llm():
    llm = FakeLLM({})
    out = run_explain(LLMExplainer(llm), verdicts=[], final_decision="GO")
    result = out["explanation"]
    assert (result.source, result.findings, llm.calls) == ("gabarit", [], [])
    assert result.reasons == ["aucun constat : rien à expliquer par le LLM"]
    assert "Aucune règle déclenchée" in result.synthesis
