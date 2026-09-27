"""Identifiant d'un contrat (`domain/identifiers.py`) : une seule règle de validité,
appliquée à la création d'un contrat, par la CLI comme par l'interface (décision du
27/09). Les contrats existants restent lisibles (`tests/test_service.py`)."""

import pytest

from cdg.domain.identifiers import ContractIdError, check_contract_id

VALID = [
    "c1",
    "demo-06-no-go-conseil",  # les deux contrats du vrai journal, le 27/09
    "demo-11-interface",
    "contrat-20260927-120000-ab12",  # identifiant par défaut de l'interface
    "test-3f2b8c1e-7d4a-4c1b-9a4e-0b1c2d3e4f5a",  # identifiant des tests PostgreSQL
    "c-2026.09_v1",
    "A" * 100,
]
INVALID = [
    "",
    ".",
    "..",
    "a/b",
    "/a",
    "a\\b",
    "a b",
    "é",
    "-a",
    ".a",
    "_a",
    "a\n",
    "a" * 101,
    "a#b",
    "a?b",
    "a:b",
    "a%2Fb",
]


@pytest.mark.parametrize("contract_id", VALID)
def test_identifiant_valide(contract_id):
    assert check_contract_id(contract_id) == contract_id


@pytest.mark.parametrize("contract_id", INVALID)
def test_identifiant_invalide_refuse_avec_la_regle(contract_id):
    with pytest.raises(ContractIdError) as exc:
        check_contract_id(contract_id)
    message = str(exc.value)
    assert message.startswith("identifiant de contrat invalide")
    assert "lettres, chiffres" in message and "100 caractères au plus" in message
