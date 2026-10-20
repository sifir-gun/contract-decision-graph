"""Chargement et validation de config/decision.yaml (spec, « Configuration »)."""

import copy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from cdg.domain import profiles, verification
from cdg.domain.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    DecisionConfig,
    load_config,
)
from cdg.domain.models import Clause


@pytest.fixture
def raw() -> dict:
    return yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data) -> Path:
    path = tmp_path / "decision.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_configuration_du_projet_conforme_a_la_spec():
    cfg = load_config()
    assert (
        cfg.weights.juridique,
        cfg.weights.financier,
        cfg.weights.conformite,
        cfg.weights.operationnel,
    ) == (0.30, 0.25, 0.25, 0.20)
    assert (cfg.thresholds.go, cfg.thresholds.go_reserves) == (0.75, 0.50)
    assert cfg.min_margin == 0.05
    assert cfg.conflict_gap == 0.5
    assert cfg.budget.max_tokens_per_contract == 60_000
    assert cfg.extraction.max_attempts == 2
    j, f, o = cfg.rules.juridique, cfg.rules.financier, cfg.rules.operationnel
    assert (j.supplier_cap_min_pct, j.supplier_cap_score_penalty) == (100, 0.5)
    assert (f.execution_penalties_min_cap_pct, f.execution_penalties_score_penalty) == (
        5,
        0.4,
    )
    assert (
        f.payment_delay_max_days_invoice,
        f.payment_delay_max_days_end_of_month,
        f.payment_delay_max_days_periodic_invoice,
        f.payment_delay_score_penalty,
    ) == (60, 45, 45, 0.2)
    assert (o.commitment_max_months, o.commitment_score_penalty) == (36, 0.3)
    assert (o.notice_max_months, o.notice_score_penalty) == (6, 0.3)
    c = cfg.rules.conformite
    assert c.transfer_safeguards == [
        "decision_adequation",
        "clauses_contractuelles_types",
        "clauses_contractuelles_ad_hoc_autorisees",
        "regles_entreprise_contraignantes",
        "code_conduite",
        "certification",
    ]
    assert c.transfer_authorization_to_verify == ["clauses_contractuelles_ad_hoc"]
    assert c.transfer_authorization_score_penalty == 0.3
    assert c.unlocated_data_score_penalty == 0.3
    assert cfg.human_policy.allowed_decisions == ["GO", "GO_RESERVES", "NO_GO"]
    assert cfg.human_policy.allow_block_override is True
    assert cfg.human_policy.hard_block_review is False
    assert cfg.llm.provider == "mistral" and cfg.llm.temperature == 0
    assert cfg.llm.models["mistral"].model_dump() == {
        "main": "mistral-small-2603",
        "light": "ministral-8b-2512",
    }
    assert cfg.llm.models["anthropic"].model_dump() == {
        "main": "claude-sonnet-5",
        "light": "claude-haiku-4-5-20251001",
    }
    assert cfg.llm.model("light") == "ministral-8b-2512"
    assert cfg.crag.model_dump() == {
        "top_k": 4,
        "max_passes": 2,
        "search": {"distinct_references": True},  # ADR 006
    }
    assert cfg.analyst_retry.model_dump() == {
        "max_attempts": 3,
        "initial_interval_seconds": 1.0,
        "backoff_factor": 2.0,
        "max_interval_seconds": 10.0,
        "jitter": False,
    }
    assert cfg.extraction_retry.model_dump() == {
        "max_attempts": 3,
        "initial_interval_seconds": 2.0,
        "backoff_factor": 2.0,
        "max_interval_seconds": 20.0,
        "jitter": False,
    }
    assert cfg.embedding.model_dump() == {
        "model": "intfloat/multilingual-e5-large",
        "dimension": 1024,
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
    }


def test_poids_par_domaine():
    cfg = load_config()
    assert cfg.weight("financier") == 0.25


def test_configuration_immuable():
    cfg = load_config()
    with pytest.raises(ValidationError):
        cfg.min_margin = 0.5


def _mutate(raw: dict, path: str, value) -> dict:
    data = copy.deepcopy(raw)
    *parents, leaf = path.split(".")
    node = data
    for key in parents:
        node = node[key]
    if value is _DELETE:
        del node[leaf]
    else:
        node[leaf] = value
    return data


_DELETE = object()


@pytest.mark.parametrize(
    "path,value",
    [
        ("weights.juridique", 0.25),  # somme des poids 0,95
        ("weights.operationnel", _DELETE),  # domaine manquant
        ("weights.fiscal", 0.0),  # domaine inconnu
        ("seuil_secret", 1),  # clé inconnue au premier niveau
        ("thresholds.go_reserves", 0.75),  # seuils non ordonnés
        ("thresholds.go", 1.2),  # seuil hors de ]0, 1]
        ("min_margin", -0.01),
        ("conflict_gap", 0),
        ("budget.max_tokens_per_contract", 0),
        ("budget.max_tokens_per_contract", True),  # booléen refusé (mode strict)
        ("budget.max_tokens_per_contract", "60000"),  # chaîne refusée (mode strict)
        ("extraction.max_attempts", 0),
        ("rules.juridique.supplier_cap_score_penalty", -0.1),
        ("rules.operationnel.notice_score_penalty", 1.5),
        ("rules.financier.execution_penalties_min_cap_pct", -5),
        ("rules.financier.payment_delay_max_days_invoice", -1),
        ("rules.financier.payment_delay_max_days_periodic_invoice", _DELETE),
        ("rules.financier.payment_delay_score_penalty", 1.5),
        ("rules.financier.late_penalties_min_cap_pct", 5),  # ancienne clé refusée
        ("rules.operationnel", _DELETE),
        ("human_policy", _DELETE),
        ("human_policy.allowed_decisions", []),
        ("human_policy.allowed_decisions", ["GO", "GO_RESERVES"]),  # NO_GO requis
        (
            "human_policy.allowed_decisions",
            ["GO", "NO_GO", "ESCALADE"],
        ),  # l'humain tranche
        ("human_policy.allowed_decisions", ["GO", "NO_GO", "NO_GO"]),  # doublon
        ("human_policy.allowed_decisions", ["GO", "NO_GO", "PEUT_ETRE"]),
        ("human_policy.allow_block_override", "oui"),  # mode strict
        ("human_policy.hard_block_review", _DELETE),  # réglage explicite
        ("human_policy.hard_block_review", "non"),  # mode strict
        ("llm.provider", "openai"),  # fournisseur sans modèles configurés
        ("llm.models.mistral.main", "mistral-small-latest"),  # alias mouvant refusé
        ("llm.models.mistral.light", ""),
        ("llm.models.mistral", _DELETE),  # fournisseur choisi sans modèles
        ("llm.temperature", 1.5),
        ("llm.max_output_tokens", 0),
        ("llm.timeout_seconds", 0),
        ("embedding.dimension", 0),
        ("rules.conformite.transfer_safeguards", []),
        (
            "rules.conformite.transfer_safeguards",
            ["aucune_garantie"],
        ),  # pas une garantie
        ("rules.conformite.transfer_safeguards", ["sans_transfert"]),
        ("rules.conformite.transfer_safeguards", ["certification", "certification"]),
        ("rules.conformite.unlocated_data_score_penalty", 1.5),
        (
            "rules.conformite.transfer_authorization_to_verify",
            ["certification"],
        ),  # déjà garantie
        ("rules.conformite.transfer_authorization_to_verify", ["aucune_garantie"]),
        ("rules.conformite.transfer_authorization_score_penalty", 1.5),
        ("rules.conformite.transfer_authorization_score_penalty", _DELETE),
        ("embedding.model", ""),
        ("crag", _DELETE),
        ("crag.top_k", 0),
        ("crag.max_passes", 0),
        ("crag.max_passes", 2.0),  # mode strict
        ("crag.search", _DELETE),  # réglage explicite de la recherche (ADR 006)
        ("crag.search.distinct_references", _DELETE),
        ("crag.search.distinct_references", "oui"),  # mode strict
        ("analyst_retry", _DELETE),
        ("analyst_retry.max_attempts", 0),
        ("analyst_retry.initial_interval_seconds", -1.0),
        ("analyst_retry.backoff_factor", 0.5),  # intervalle décroissant
        ("analyst_retry.jitter", "non"),
        ("extraction_retry", _DELETE),
        ("extraction_retry.max_attempts", 0),
        # section du profil sous_traitance (PR 2)
        ("sous_traitance.regle_secrete", 1),  # clé inconnue
        ("sous_traitance.rules", _DELETE),
        ("sous_traitance.absence_terms", _DELETE),
        ("sous_traitance.category_terms", _DELETE),
        ("sous_traitance.rules.conformite", _DELETE),
        ("sous_traitance.rules.conformite.insurance_min_eur", _DELETE),
        ("sous_traitance.rules.conformite.insurance_min_eur", -1),
        ("sous_traitance.rules.conformite.insurance_min_eur", "2000000"),  # strict
        ("sous_traitance.rules.conformite.insurance_score_penalty", 1.5),
        ("sous_traitance.rules.juridique.design_score_penalty", -0.1),
        ("sous_traitance.rules.juridique.design_blocking", _DELETE),
        ("sous_traitance.rules.juridique.design_blocking", ["reutilisation"]),
        ("sous_traitance.rules.juridique.subcontractor_cap_min_pct", -1),
        # un réglage du profil fournisseur n'est pas un réglage de la sous-traitance
        ("sous_traitance.rules.financier.execution_penalties_min_cap_pct", 5),
        (
            "sous_traitance.rules.financier.payment_delay_max_days_periodic_invoice",
            _DELETE,
        ),
        ("sous_traitance.rules.financier.payment_delay_score_penalty", 1.5),
        ("sous_traitance.rules.operationnel.schedule_accepted", ["indicatif"]),
        ("sous_traitance.rules.operationnel.schedule_score_penalty", _DELETE),
    ],
)
def test_configuration_invalide_refusee(tmp_path, raw, path, value):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, _mutate(raw, path, value)))


def test_valeur_par_defaut_reservee_aux_configurations_archivees(raw):
    """Un réglage ajouté après l'archive prend, dans le modèle, la valeur du comportement
    d'avant (relecture d'une configuration archivée) ; le fichier du projet doit pourtant
    le régler explicitement, comme tout le reste."""
    data = _mutate(raw, "crag.search", _DELETE)
    assert DecisionConfig.model_validate(data).crag.search.distinct_references is False


@pytest.mark.parametrize(
    ("path", "named"),
    [
        ("crag.search", "crag.search"),
        ("crag.search.distinct_references", "crag.search"),
    ],
)
def test_reglage_absent_du_fichier_nomme(tmp_path, raw, path, named):
    with pytest.raises(ConfigError, match=f"réglages absents.*{named}"):
        load_config(_write(tmp_path, _mutate(raw, path, _DELETE)))


def test_fichier_absent(tmp_path):
    with pytest.raises(ConfigError, match="introuvable"):
        load_config(tmp_path / "absent.yaml")


def test_yaml_illisible(tmp_path):
    path = tmp_path / "decision.yaml"
    path.write_text("weights: [juridique: 0.3", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML"):
        load_config(path)


def test_yaml_qui_n_est_pas_un_dictionnaire(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, [1, 2, 3]))


def test_config_modifiee_valide_directement(raw):
    data = _mutate(raw, "conflict_gap", 1.0)
    assert DecisionConfig.model_validate(data).conflict_gap == 1.0


# --- section sous_traitance (PR 2, spec « Configuration », ADR 006) --------------------


def test_section_sous_traitance_exigee_dans_le_fichier(tmp_path, raw):
    """Absente du fichier du projet, la section arrête le programme : seul le modèle la
    laisse absente par défaut, pour relire les configurations archivées."""
    with pytest.raises(ConfigError, match="réglages absents.*sous_traitance"):
        load_config(_write(tmp_path, _mutate(raw, "sous_traitance", _DELETE)))


def test_section_sous_traitance_nulle_refusee_dans_le_fichier(tmp_path, raw):
    # `null` n'est que la valeur par défaut du modèle : le fichier règle la section
    with pytest.raises(ConfigError, match="réglages absents.*sous_traitance"):
        load_config(_write(tmp_path, _mutate(raw, "sous_traitance", None)))


def test_configuration_archivee_sans_section_relue(raw):
    """Une configuration d'avant la PR 2 n'a pas la section : elle se relit telle quelle,
    sans profil sous_traitance (aucune analyse sous ce profil n'était possible)."""
    old = _mutate(raw, "sous_traitance", _DELETE)
    assert DecisionConfig.model_validate(old).sous_traitance is None


def _sous_traitance(change) -> dict:
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    change(data["sous_traitance"])
    return data


@pytest.mark.parametrize(
    ("type_", "path", "value", "reason"),
    [
        # une catégorie dans deux listes
        (
            "protection_design",
            "juridique.design_penalized",
            ["confidentialite_seule", "reutilisation_autorisee"],
            "plus d'une fois",
        ),
        (
            "protection_design",
            "juridique.design_accepted",
            ["usage_limite_au_projet", "confidentialite_seule"],
            "plus d'une fois",
        ),
        (
            "obligation_certification",
            "conformite.certification_accepted",
            ["norme_et_preuves", "conformite_generale"],
            "plus d'une fois",
        ),
        (
            "delai_execution",
            "operationnel.schedule_penalized",
            ["delai_indicatif", "date_contractuelle"],
            "plus d'une fois",
        ),
        # deux fois dans la même liste
        (
            "delai_execution",
            "operationnel.schedule_penalized",
            ["delai_indicatif", "delai_indicatif"],
            "plus d'une fois",
        ),
        # dans aucune liste
        ("protection_design", "juridique.design_blocking", [], "aucune liste"),
        (
            "obligation_certification",
            "conformite.certification_penalized",
            ["norme_sans_preuves"],
            "aucune liste",
        ),
        ("delai_execution", "operationnel.schedule_penalized", [], "aucune liste"),
    ],
)
def test_categories_reparties_une_fois(raw, type_, path, value, reason):
    """Chaque catégorie d'un type est dans exactement une liste ; l'erreur nomme le
    type."""
    data = _mutate(raw, f"sous_traitance.rules.{path}", value)
    with pytest.raises(ValidationError, match=f"{type_} : .*{reason}"):
        DecisionConfig.model_validate(data)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda s: s["absence_terms"].pop("assurance_sous_traitant"),
            r"absence_terms : types manquants \['assurance_sous_traitant'\]",
        ),
        (
            # un type du profil fournisseur n'est pas un type de la sous-traitance
            lambda s: s["absence_terms"].update({"penalites_execution": ["x"]}),
            r"absence_terms : .*types inconnus \['penalites_execution'\]",
        ),
        (
            lambda s: s["absence_terms"]["revision_prix"].append("Indexation"),
            "absence_terms : terme répété pour revision_prix",
        ),
        (
            lambda s: s["category_terms"].pop("delai_paiement"),
            r"category_terms : types manquants \['delai_paiement'\]",
        ),
        (
            lambda s: s["category_terms"].update(
                {"transfert_hors_ue": {"decision_adequation": ["x"]}}
            ),
            r"category_terms : .*types inconnus \['transfert_hors_ue'\]",
        ),
        (
            lambda s: s["category_terms"]["delai_execution"].update(
                {"date_facture": ["x"]}
            ),
            "category_terms : catégorie inconnue pour delai_execution : date_facture",
        ),
        (
            lambda s: s["category_terms"]["protection_design"].update(
                {"norme_et_preuves": ["x"]}
            ),
            "catégorie inconnue pour protection_design : norme_et_preuves",
        ),
        (
            lambda s: s["category_terms"]["protection_design"][
                "confidentialite_seule"
            ].append("Seules fins"),
            "category_terms : terme répété pour protection_design",
        ),
    ],
)
def test_termes_sous_traitance_valides_contre_le_profil(change, message):
    """Les termes de la section sont validés contre les types du profil sous_traitance,
    non contre ceux du fournisseur."""
    with pytest.raises(ValidationError, match=message):
        DecisionConfig.model_validate(_sous_traitance(change))


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s["absence_terms"].update({"delai_execution": []}),
        lambda s: s["category_terms"]["delai_execution"].update(
            {"delai_indicatif": []}
        ),
        lambda s: s["absence_terms"]["delai_execution"].append(""),
    ],
)
def test_termes_sous_traitance_jamais_vides(change):
    with pytest.raises(ValidationError):
        DecisionConfig.model_validate(_sous_traitance(change))


def test_termes_fournisseur_toujours_valides_contre_le_profil_fournisseur(raw):
    # la validation partagée garde ses messages : les types de la sous-traitance sont
    # inconnus de la section du fournisseur
    data = copy.deepcopy(raw)
    data["extraction"]["absence_terms"]["delai_execution"] = ["x"]
    with pytest.raises(ValidationError, match=r"types inconnus \['delai_execution'\]"):
        DecisionConfig.model_validate(data)


def test_valeurs_de_demonstration():
    """Valeurs de départ de la sous-traitance, à valider avec le cabinet (spec,
    « Questions »), lues dans le fichier du projet."""
    section = load_config().sous_traitance
    assert section is not None
    r = section.rules
    j, f, c, o = r.juridique, r.financier, r.conformite, r.operationnel
    assert (j.subcontractor_cap_min_pct, j.subcontractor_cap_score_penalty) == (
        100,
        0.5,
    )
    assert (j.design_accepted, j.design_penalized, j.design_blocking) == (
        ["usage_limite_au_projet"],
        ["confidentialite_seule"],
        ["reutilisation_autorisee"],
    )
    assert j.design_score_penalty == 0.3
    assert (
        f.execution_delay_penalties_min_cap_pct,
        f.execution_delay_penalties_score_penalty,
    ) == (5, 0.4)
    assert (
        f.payment_delay_max_days_invoice,
        f.payment_delay_max_days_end_of_month,
        f.payment_delay_max_days_periodic_invoice,
        f.payment_delay_score_penalty,
    ) == (60, 45, 45, 0.2)
    assert (c.certification_accepted, c.certification_penalized) == (
        ["norme_et_preuves"],
        ["norme_sans_preuves", "conformite_generale"],
    )
    assert c.certification_score_penalty == 0.3
    assert (c.insurance_min_eur, c.insurance_score_penalty) == (2_000_000, 0.3)
    assert (o.schedule_accepted, o.schedule_penalized) == (
        ["date_contractuelle"],
        ["delai_indicatif"],
    )
    assert o.schedule_score_penalty == 0.4


def test_termes_de_paiement_de_la_sous_traitance_ceux_du_fournisseur():
    cfg = load_config()
    assert cfg.sous_traitance is not None
    assert (
        cfg.sous_traitance.category_terms["delai_paiement"]
        == cfg.extraction.category_terms["delai_paiement"]
    )
    assert (
        cfg.sous_traitance.absence_terms["delai_paiement"]
        == cfg.extraction.absence_terms["delai_paiement"]
    )


def test_section_sous_traitance_couvre_les_types_du_profil():
    section = load_config().sous_traitance
    assert section is not None
    spec = profiles.SOUS_TRAITANCE
    assert set(section.absence_terms) == set(spec.kinds)
    assert set(section.category_terms) == set(spec.kind_categories)
    for kind, terms in section.absence_terms.items():
        assert len(terms) >= 2, kind
    for kind, by_category in section.category_terms.items():
        assert set(by_category) == set(spec.kind_categories[kind]), kind


# --- termes de la sous-traitance, sur des citations types -------------------------------
# Les contrôles de l'extraction (`domain/verification.py`) lisent ces termes : une liste
# trop large redemande à tort une clause bien extraite, une liste trop étroite laisse
# passer une catégorie contredite par la citation.


def _clause(kind: str, quote: str, category: str | None = None) -> Clause:
    return Clause(kind=kind, present=True, quote=quote, value=None, category=category)


@pytest.mark.parametrize(
    ("kind", "quote", "expected"),
    [
        (
            "protection_design",
            (
                "Le sous-traitant est libre de réutiliser les dessins remis pour d'autres "
                "clients."
            ),
            "reutilisation_autorisee",
        ),
        (
            "protection_design",
            (
                "Les dessins et plans remis ne sont utilisés qu'aux seules fins de la "
                "commande et restent confidentiels."
            ),
            "usage_limite_au_projet",
        ),
        (
            "protection_design",
            "Le sous-traitant s'engage à garder confidentiels les dessins remis.",
            "confidentialite_seule",
        ),
        (
            "obligation_certification",
            (
                "Le sous-traitant joint le procès-verbal d'essai et le certificat de "
                "conformité à la norme applicable à chaque lot."
            ),
            "norme_et_preuves",
        ),
        (
            "obligation_certification",
            "Les pièces sont conformes aux normes en vigueur.",
            "conformite_generale",
        ),
        (
            # « certification » n'est pas un certificat remis : pas une preuve
            "obligation_certification",
            (
                "Le sous-traitant, titulaire de la certification ISO 9001, livre des "
                "pièces conformes aux normes en vigueur."
            ),
            "conformite_generale",
        ),
        (
            "obligation_certification",
            "Le sous-traitant fournit le certificat de conformité de chaque lot.",
            "norme_et_preuves",
        ),
        (
            "obligation_certification",
            "Les pièces sont conformes à la norme applicable à leur famille.",
            "norme_sans_preuves",
        ),
        (
            # aucune norme nommée, même avec des preuves : la conformité générale (pénalisée)
            # l'emporte sur `norme_et_preuves` (acceptée), dans le sens prudent
            "obligation_certification",
            (
                "Les pièces sont conformes aux normes en vigueur ; le sous-traitant remet "
                "les procès-verbaux d'essais."
            ),
            "conformite_generale",
        ),
        (
            # st-01 : une forme verbale active, où une négation s'insérerait entre le verbe
            # et son objet
            "obligation_certification",
            (
                "Les pièces sont conformes à la norme NF X 00-000 ; le sous-traitant remet "
                "les procès-verbaux d'essais à la livraison."
            ),
            "norme_et_preuves",
        ),
        (
            "obligation_certification",
            (
                "Les pièces sont conformes à la norme NF X 00-000 ; aucun procès-verbal "
                "d'essai n'est exigé."
            ),
            "norme_sans_preuves",
        ),
        # preuves remises : formes verbales actives suivies d'un article défini
        (
            "obligation_certification",
            (
                "Les pièces sont conformes à la norme NF X 00-000 ; le sous-traitant "
                "remettra le procès-verbal d'essai avant la livraison."
            ),
            "norme_et_preuves",
        ),
        (
            "obligation_certification",
            (
                "Les pièces sont conformes à la norme NF X 00-000 ; le sous-traitant "
                "joint les certificats de conformité à chaque lot."
            ),
            "norme_et_preuves",
        ),
        (
            "obligation_certification",
            (
                "Les pièces sont conformes à la norme NF X 00-000 ; le sous-traitant "
                "fournit les rapports d'essais."
            ),
            "norme_et_preuves",
        ),
        (
            "obligation_certification",
            (
                "Les pièces sont conformes à la norme NF X 00-000 ; le sous-traitant "
                "fournira le rapport d'essai de chaque lot."
            ),
            "norme_et_preuves",
        ),
        # participe, « accompagné(e)(s) des … », « livré avec un … » : une négation les
        # contient aussi (« aucun … remis », « ne sont pas accompagnées des … »), ils
        # n'évoquent donc plus l'acceptation ; la norme nommée les fait lire sans preuves,
        # dans le sens prudent
        (
            "obligation_certification",
            (
                "Les pièces sont conformes à la norme NF X 00-000, procès-verbaux d'essais "
                "remis à la livraison."
            ),
            "norme_sans_preuves",
        ),
        (
            "obligation_certification",
            (
                "Les pièces sont conformes à la norme NF X 00-000 et accompagnées des "
                "certificats de conformité."
            ),
            "norme_sans_preuves",
        ),
        (
            "obligation_certification",
            (
                "Les pièces sont conformes à la norme NF X 00-000 ; chaque lot est livré "
                "avec un rapport d'essai."
            ),
            "norme_sans_preuves",
        ),
        (
            # procès-verbal de réception : pas une preuve d'essai
            "obligation_certification",
            (
                "Les pièces sont conformes à la norme NF X 00-000 ; le sous-traitant "
                "remet les procès-verbaux de réception signés."
            ),
            "norme_sans_preuves",
        ),
        (
            "protection_design",
            (
                "Les dessins remis sont confidentiels et ne peuvent être utilisés que pour "
                "le présent projet."
            ),
            "usage_limite_au_projet",
        ),
        (
            "protection_design",
            "Les dessins remis sont confidentiels et ne servent que pour le projet.",
            "usage_limite_au_projet",
        ),
        (
            "protection_design",
            (
                "Les plans remis restent confidentiels et ne valent que pour le présent "
                "projet."
            ),
            "usage_limite_au_projet",
        ),
        (
            "protection_design",
            (
                "Les pièces dessinées remises sont confidentielles et ne peuvent être "
                "utilisées que pour la commande."
            ),
            "usage_limite_au_projet",
        ),
        (
            "protection_design",
            (
                "Le dossier remis est confidentiel et ne peut être utilisé que pour la "
                "commande."
            ),
            "usage_limite_au_projet",
        ),
        (
            "protection_design",
            (
                "La documentation remise est confidentielle et ne peut être utilisée que "
                "pour la commande."
            ),
            "usage_limite_au_projet",
        ),
        (
            "delai_execution",
            (
                "Le sous-traitant livre au plus tard le 15 mars, selon le planning "
                "contractuel."
            ),
            "date_contractuelle",
        ),
        (
            "delai_execution",
            "La livraison est prévue au plus tard le 15 mars, à titre indicatif.",
            "delai_indicatif",
        ),
        (
            "delai_execution",
            "Le délai d'exécution est indicatif.",
            "delai_indicatif",
        ),
        (
            "delai_paiement",
            "Les factures sont payables à 45 jours fin de mois.",
            "fin_de_mois",
        ),
        (
            "delai_paiement",
            "Les factures sont payables à 60 jours à compter de la date de facture.",
            "date_facture",
        ),
        (
            "delai_paiement",
            "Les factures périodiques sont payables à 45 jours.",
            "facture_periodique",
        ),
    ],
)
def test_citation_sous_traitance_evoque_sa_categorie(kind, quote, expected):
    """Une seule catégorie est acceptée pour la citation : celle que ses termes évoquent
    (la première, de la plus spécifique à la moins spécifique)."""
    section = load_config().sous_traitance
    assert section is not None
    for category in profiles.SOUS_TRAITANCE.kind_categories[kind]:
        problems = verification.category_mismatches(
            [_clause(kind, quote, category)], section.category_terms
        )
        assert (problems == []) == (category == expected), (category, problems)


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        (
            "responsabilite_donneur_ordre",
            "La responsabilité du donneur d'ordre est limitée à 150 % du montant.",
        ),
        (
            "responsabilite_sous_traitant",
            "Le sous-traitant répond de tout dommage causé aux pièces confiées.",
        ),
        (
            "protection_design",
            "Les dessins remis restent la propriété du donneur d'ordre.",
        ),
        (
            "revision_prix",
            "Les prix sont révisés chaque année selon l'indice du secteur.",
        ),
        ("delai_paiement", "Les factures sont payables à 45 jours fin de mois."),
        (
            "penalites_retard_execution",
            "Tout retard d'exécution donne lieu à des pénalités de retard d'exécution.",
        ),
        (
            "obligation_certification",
            "Le sous-traitant remet un certificat de conformité avec chaque lot.",
        ),
        (
            "assurance_sous_traitant",
            "Le sous-traitant remet une attestation d'assurance avant le démarrage.",
        ),
        (
            "assurance_sous_traitant",
            "Le sous-traitant est assuré auprès d'un assureur notoirement solvable.",
        ),
        (
            "obligation_certification",
            "Chaque lot est livré avec son procès-verbal d'essai.",
        ),
        ("delai_execution", "Le délai d'exécution court à compter de la commande."),
        ("delai_execution", "Le planning de fabrication est annexé au contrat."),
    ],
)
def test_absence_sous_traitance_evoquee_par_le_texte(kind, text):
    section = load_config().sous_traitance
    assert section is not None
    absent = Clause(kind=kind, present=False, quote="", value=None)
    problems = verification.mentioned_absences(text, [absent], section.absence_terms)
    assert len(problems) == 1 and problems[0].endswith(kind)


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        # pénalités de retard de paiement du donneur d'ordre : pas des pénalités
        # d'exécution du sous-traitant (C. com., art. L441-10, II)
        (
            "penalites_retard_execution",
            (
                "Tout retard de paiement rend exigibles des pénalités de retard égales à "
                "trois fois le taux d'intérêt légal."
            ),
        ),
        # pénalités de retard de paiement, sans « retard de » : toujours pas d'exécution
        (
            "penalites_retard_execution",
            (
                "Tout retard de paiement donne lieu à des pénalités égales à trois fois "
                "le taux d'intérêt légal."
            ),
        ),
        (
            "penalites_retard_execution",
            "Le donneur d'ordre est redevable de pénalités en cas de retard de paiement.",
        ),
        # procès-verbal de réception : pas des preuves de conformité à une norme
        (
            "obligation_certification",
            "Un procès-verbal de réception est signé des deux parties à la livraison.",
        ),
        # assurance qualité : pas une assurance
        (
            "assurance_sous_traitant",
            "Le sous-traitant applique un plan d'assurance qualité conforme à son manuel.",
        ),
        (
            "assurance_sous_traitant",
            "La responsabilité du sous-traitant est plafonnée à 150 000 € par sinistre.",
        ),
        # certificat d'assurance : pas une obligation de certification
        (
            "obligation_certification",
            "Le sous-traitant remet un certificat d'assurance avant le démarrage.",
        ),
        (
            "responsabilite_sous_traitant",
            "Le sous-traitant répond aux demandes du donneur d'ordre sous 48 heures.",
        ),
        (
            "delai_execution",
            "Les factures sont payables à 45 jours fin de mois.",
        ),
    ],
)
def test_absence_sous_traitance_non_evoquee_par_un_texte_voisin(kind, text):
    section = load_config().sous_traitance
    assert section is not None
    absent = Clause(kind=kind, present=False, quote="", value=None)
    assert verification.mentioned_absences(text, [absent], section.absence_terms) == []


# --- preuves de conformité : la catégorie acceptée n'est évoquée que par une preuve remise
# Les termes de `norme_et_preuves` (acceptée) sont des formes verbales actives suivies d'un
# article défini (« remet les procès-verbaux d'essais ») : une négation s'insère entre le
# verbe et son objet (« ne remet pas les »), si bien qu'une négation des preuves n'évoque
# jamais l'acceptation, et qu'un retour de vérification ne pousse jamais le modèle vers
# elle. Ni participe (« aucun … remis »), ni « accompagné(e)(s) des … » (« ne sont pas
# accompagnées des … »), ni « livré avec un … » (« n'est pas livré avec un … »), qu'une
# négation contient aussi. Les termes des catégories pénalisées restent larges.

PROOFS_SINGULAR = (
    "procès-verbal d'essai",
    "certificat de conformité",
    "certificat d'essai",
    "rapport d'essai",
)
PROOFS_PLURAL = (
    "procès-verbaux d'essais",
    "certificats de conformité",
    "certificats d'essai",
    "rapports d'essais",
)
NEGATIONS = [
    *(
        form.format(proof)
        for proof in PROOFS_SINGULAR
        for form in (
            "aucun {} n'est exigé",
            "aucun {} n'est remis",
            "aucun {} n'est à remettre",
            "sans {}",
        )
    ),
    *(
        form.format(proof)
        for proof in PROOFS_PLURAL
        for form in ("sans {}", "{} non exigés")
    ),
    # « … à remettre » et « … remis » après une négation : jamais un terme de la catégorie
    # acceptée
    *(
        form.format(proof)
        for proof in PROOFS_SINGULAR + PROOFS_PLURAL
        for form in (
            "aucun {} à remettre",
            "sans {} à remettre",
            "pas de {} à remettre",
            "aucun {} remis",
            "sans {} remis",
            "pas de {} remis",
        )
    ),
    # négation de la phrase entière
    *(
        form.format(proof)
        for proof in PROOFS_PLURAL
        for form in ("mais ne sont pas accompagnées des {}",)
    ),
    *(
        form.format(proof)
        for proof in PROOFS_SINGULAR
        for form in (
            "le lot n'est pas livré avec un {}",
            "le lot n'est pas accompagné d'un {}",
        )
    ),
    # négation du verbe actif : elle s'insère entre le verbe et son objet
    *(
        form.format(proof)
        for proof in PROOFS_PLURAL
        for form in (
            "le sous-traitant ne remet pas les {}",
            "le sous-traitant ne remettra pas les {}",
            "sans remettre les {}",
            "le sous-traitant n'est pas tenu de remettre les {}",
            "le sous-traitant ne fournit pas les {}",
            "le sous-traitant ne fournira pas les {}",
            "le sous-traitant ne joint pas les {}",
        )
    ),
    *(
        form.format(proof)
        for proof in PROOFS_SINGULAR
        for form in (
            "le sous-traitant ne remet pas le {}",
            "le sous-traitant ne remet aucun {}",
            "aucun {} n'est joint",
            "le sous-traitant ne fournit pas le {}",
            "le sous-traitant ne joint pas le {}",
        )
    ),
]


@pytest.mark.parametrize("negation", NEGATIONS)
def test_negation_des_preuves_n_evoque_jamais_norme_et_preuves(negation):
    section = load_config().sous_traitance
    assert section is not None
    quote = f"Les pièces sont conformes à la norme NF X 00-000, {negation}."
    sans_preuves = [_clause("obligation_certification", quote, "norme_sans_preuves")]
    assert verification.category_mismatches(sans_preuves, section.category_terms) == []
    problems = verification.category_mismatches(
        [_clause("obligation_certification", quote, "norme_et_preuves")],
        section.category_terms,
    )
    assert len(problems) == 1, problems
    assert problems[0].startswith("catégorie contredite par la citation"), problems
    assert "norme_sans_preuves" in problems[0], problems


# --- réutilisation des dessins : lue dans le sens prudent (blocage dur) ------------------
# « réutiliser » et « librement » évoquent `reutilisation_autorisee`, la première
# catégorie : une citation qui l'évoque n'est jamais lue `confidentialite_seule` ni
# `usage_limite_au_projet`, au prix d'une redemande quand le texte nie la réutilisation.


@pytest.mark.parametrize(
    "quote",
    [
        "Le sous-traitant peut réutiliser les dessins remis, qui restent confidentiels.",
        "Le sous-traitant peut les réutiliser pour d'autres clients.",
        (
            "Le sous-traitant est autorisé à les réutiliser, les dessins restant "
            "confidentiels."
        ),
        "Les dessins remis, confidentiels, sont à réutiliser selon son besoin.",
        (
            "Le sous-traitant utilise librement les dessins remis, qui restent "
            "confidentiels."
        ),
        # autres formes du verbe et du nom (2e passe de correction)
        "Réutilisation libre des dessins remis, qui restent confidentiels.",
        "La libre réutilisation des dessins remis, confidentiels, est admise.",
        "Le sous-traitant réutilise les dessins remis pour d'autres clients.",
        "Les dessins remis, confidentiels, sont réutilisables par le sous-traitant.",
    ],
)
@pytest.mark.parametrize(
    "rendered", ["confidentialite_seule", "usage_limite_au_projet"]
)
def test_reutilisation_evoquee_contredit_une_categorie_prudente(quote, rendered):
    section = load_config().sous_traitance
    assert section is not None
    problems = verification.category_mismatches(
        [_clause("protection_design", quote, rendered)], section.category_terms
    )
    assert len(problems) == 1 and "reutilisation_autorisee" in problems[0], problems
    assert (
        verification.category_mismatches(
            [_clause("protection_design", quote, "reutilisation_autorisee")],
            section.category_terms,
        )
        == []
    )


def test_negation_de_la_reutilisation_redemandee_sens_prudent():
    """Voulu : « n'est autorisé à réutiliser que pour » contient « réutiliser ». Une
    nouvelle extraction, puis la revue humaine, valent mieux qu'une autorisation de
    réutilisation lue comme un usage limité."""
    section = load_config().sous_traitance
    assert section is not None
    quote = (
        "Le sous-traitant n'est autorisé à réutiliser les dessins remis que pour la "
        "commande."
    )
    problems = verification.category_mismatches(
        [_clause("protection_design", quote, "usage_limite_au_projet")],
        section.category_terms,
    )
    assert len(problems) == 1 and "reutilisation_autorisee" in problems[0], problems


@pytest.mark.parametrize(
    ("kind", "quote"),
    [
        # aucune norme nommée n'est évoquée : `conformite_generale` ou `norme_sans_preuves`
        # se juge ailleurs que sur ces mots (spec : conformité générale = sans norme nommée)
        (
            "obligation_certification",
            "Les pièces sont conformes aux normes du secteur.",
        ),
        (
            "obligation_certification",
            "Le sous-traitant respecte les normes du secteur.",
        ),
    ],
)
def test_citation_sans_terme_de_categorie_non_controlee(kind, quote):
    section = load_config().sous_traitance
    assert section is not None
    for category in profiles.SOUS_TRAITANCE.kind_categories[kind]:
        assert (
            verification.category_mismatches(
                [_clause(kind, quote, category)], section.category_terms
            )
            == []
        ), category
