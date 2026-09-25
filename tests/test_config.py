"""Chargement et validation de config/decision.yaml (spec, « Configuration »)."""

import copy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from cdg.domain.config import DEFAULT_CONFIG_PATH, ConfigError, DecisionConfig, load_config


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
    assert (f.late_penalties_min_cap_pct, f.late_penalties_score_penalty) == (5, 0.4)
    assert (o.commitment_max_months, o.commitment_score_penalty) == (36, 0.3)
    assert (o.notice_max_months, o.notice_score_penalty) == (6, 0.3)
    c = cfg.rules.conformite
    assert c.transfer_safeguards == [
        "decision_adequation",
        "clauses_contractuelles_types",
        "regles_entreprise_contraignantes",
        "code_conduite",
        "certification",
    ]
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
    assert cfg.crag.model_dump() == {"top_k": 4, "max_passes": 2}
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
        ("rules.financier.late_penalties_min_cap_pct", -5),
        ("rules.operationnel", _DELETE),
        ("human_policy", _DELETE),
        ("human_policy.allowed_decisions", []),
        ("human_policy.allowed_decisions", ["GO", "GO_RESERVES"]),  # NO_GO requis
        ("human_policy.allowed_decisions", ["GO", "NO_GO", "ESCALADE"]),  # l'humain tranche
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
        ("rules.conformite.transfer_safeguards", ["aucune_garantie"]),  # pas une garantie
        ("rules.conformite.transfer_safeguards", ["sans_transfert"]),
        ("rules.conformite.transfer_safeguards", ["certification", "certification"]),
        ("rules.conformite.unlocated_data_score_penalty", 1.5),
        ("embedding.model", ""),
        ("crag", _DELETE),
        ("crag.top_k", 0),
        ("crag.max_passes", 0),
        ("crag.max_passes", 2.0),  # mode strict
    ],
)
def test_configuration_invalide_refusee(tmp_path, raw, path, value):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, _mutate(raw, path, value)))


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
