"""Explication (J4) : constats rattachés à leur clause, contrôles d'une explication, gabarit,
explicateur LLM et nœud `explain` ; critère 7 : une explication qui contredit le verdict est
rejetée.

Python pur et doublures : aucun appel réseau. Le graphe complet est testé dans
`test_explain_graph.py`.
"""

import pytest
from doubles import ABSENT, CONTRACT_TEXT, FakeLLM, clauses

from cdg.application.deps import TemplateOnly
from cdg.application.explanation import LLMExplainer
from cdg.application.nodes.explain import explain
from cdg.domain import explanation
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
    return justify(RULES[domain](found, CONFIG), trace)


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


def draft(*entries, synthesis="Décision finale : NO_GO, par blocage dur.") -> Draft:
    return Draft(
        findings=list(entries) or [entry(ACCORD), entry(TRANSFERT)], synthesis=synthesis
    )


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
    ],
)
def test_articles_cites(text, found):
    assert explanation.articles(text) == found


# --- Contrôles : critère 7 ---------------------------------------------------------------------


def test_explication_fidele_acceptee():
    assert explanation.refusals(draft(), REQUEST) == []


def test_7_explication_qui_contredit_le_verdict_rejetee():
    contradictory = draft(
        synthesis="Décision finale : GO, les risques sont acceptables."
    )
    reasons = explanation.refusals(contradictory, REQUEST)
    assert any("ne nomme pas la décision finale (NO_GO)" in r for r in reasons)
    assert any(
        "autre décision que la décision finale (NO_GO) : GO" in r for r in reasons
    )


def test_7_autre_libelle_dans_un_constat_rejete():
    reasons = explanation.refusals(
        draft(
            entry(ACCORD, text="Ce point justifierait un GO_RESERVES."),
            entry(TRANSFERT),
        ),
        REQUEST,
    )
    assert reasons == [
        (
            "conformite-1 : nomme une autre décision que la décision finale (NO_GO) : "
            "GO_RESERVES"
        )
    ]


def test_7_escalade_nommee_rejetee():
    reasons = explanation.refusals(
        draft(synthesis="Décision finale : NO_GO, après escalade."), REQUEST
    )
    assert any("ESCALADE" in r for r in reasons)


def test_reference_non_retenue_rejetee():
    reasons = explanation.refusals(
        draft(entry(ACCORD, references=["C. civ., art. 1170"]), entry(TRANSFERT)),
        REQUEST,
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
        draft(entry(ACCORD), entry(TRANSFERT, references=["RGPD, art. 28"])), REQUEST
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
        draft(entry(ACCORD), entry(TRANSFERT, text=text)), REQUEST
    )
    assert reasons == [
        (
            "conformite-2 : article cité hors des références retenues pour la clause "
            "transfert_hors_ue : 28"
        )
    ]


def test_article_du_texte_du_constat_citable():
    # le constat de transfert cite lui-même l'art. 46 (texte du code, non du LLM)
    text = "Clauses ad hoc : autorisation à vérifier (art. 46, par. 3, a) RGPD)."
    assert (
        explanation.refusals(draft(entry(ACCORD), entry(TRANSFERT, text=text)), REQUEST)
        == []
    )


def test_synthese_citant_un_article_hors_des_references_rejetee():
    reasons = explanation.refusals(
        draft(synthesis="Décision finale : NO_GO (C. civ., art. 1171)."), REQUEST
    )
    assert reasons == [
        "la synthèse cite un article hors des références retenues : 1171"
    ]


def test_constats_manquants_inconnus_ou_repetes_rejetes():
    stray = ExplainedFinding(id="juridique-9", kind=None, references=[], text="x")
    reasons = explanation.refusals(draft(entry(ACCORD), entry(ACCORD), stray), REQUEST)
    assert reasons[:3] == [
        "constats non expliqués : conformite-2",
        "constats inconnus : juridique-9",
        "constats expliqués plusieurs fois : conformite-1",
    ]


def test_clause_erronee_ou_texte_vide_rejetes():
    reasons = explanation.refusals(
        draft(entry(ACCORD, kind="transfert_hors_ue"), entry(TRANSFERT, text=" ")),
        REQUEST,
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
    assert explanation.refusals(result.draft(), request) == []
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
    result, usage = LLMExplainer(llm)(REQUEST, [])
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
        REQUEST, ["essai 1 : la synthèse ne nomme pas la décision finale"]
    )
    user = llm.calls[0]["user"]
    data, feedback = user.split("Essai précédent refusé", 1)
    assert "essai 1 : la synthèse ne nomme pas" in feedback
    assert "essai 1" not in data


def test_explicateur_motif_humain_transmis_releveur_non():
    llm = FakeLLM({"explain": llm_answer(draft())})
    request = explanation.request(state(final_decision="GO", human=HUMAN))
    LLMExplainer(llm)(request, [])
    user = llm.calls[0]["user"]
    assert "risque accepté, pas un NO_GO" in user and "relecteur-synth" not in user


# --- Nœud explain --------------------------------------------------------------------------------


def never_retried(exc):
    return False


def run_explain(explainer, retrying=never_retried, **overrides):
    return explain(
        state(**overrides),
        explainer=explainer,
        decision_config=CONFIG,
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
    bad = draft(synthesis="Décision finale : GO.")
    llm = FakeLLM({"explain": [llm_answer(bad), llm_answer(draft())]})
    out = run_explain(LLMExplainer(llm))
    result = out["explanation"]
    assert (result.source, result.attempts) == ("llm", 2)
    assert result.reasons[0].startswith("essai 1 : la synthèse ne nomme pas")
    # le second essai reçoit les motifs du refus
    assert "la synthèse ne nomme pas la décision finale" in llm.calls[1]["user"]
    assert len(out["usage"]) == 2


def test_7_deux_explications_refusees_puis_gabarit():
    bad = draft(synthesis="Décision finale : GO.")
    llm = FakeLLM({"explain": [llm_answer(bad), llm_answer(bad)]})
    out = run_explain(LLMExplainer(llm))
    result = out["explanation"]
    assert (result.source, result.attempts) == ("gabarit", 2)
    assert [r.split(" : ", 1)[0] for r in result.reasons] == [
        "essai 1",
        "essai 1",
        "essai 2",
        "essai 2",
    ]
    assert len(llm.calls) == 2 and len(out["usage"]) == 2
    assert result.synthesis.startswith("Décision finale : NO_GO.")


def test_nombre_d_essais_lu_dans_la_configuration():
    config = CONFIG.model_copy(
        update={"explain": CONFIG.explain.model_copy(update={"max_attempts": 1})}
    )
    bad = draft(synthesis="Décision finale : GO.")
    llm = FakeLLM({"explain": [llm_answer(bad)]})
    out = explain(
        state(),
        explainer=LLMExplainer(llm),
        decision_config=config,
        retrying=never_retried,
    )
    assert (out["explanation"].source, len(llm.calls)) == ("gabarit", 1)


class Failing:
    def __init__(self, exc):
        self.exc, self.calls = exc, 0

    def __call__(self, request, feedback):
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
