"""Identifiant d'un contrat : une seule règle de validité, pour la CLI comme pour
l'interface (décision du 27/09).

L'identifiant nomme le thread du graphe et l'enregistrement scellé ; l'interface en fait
un segment d'adresse. Lettres ASCII, chiffres, « . », « _ » et « - », 100 caractères au
plus, en commençant par une lettre ou un chiffre : ni « / », ni blanc, ni « . » ou « .. »
seuls. La règle vaut à la création d'un contrat seulement : les contrats existants
restent lisibles, quel que soit leur identifiant.
"""

import re

CONTRACT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
RULE = (
    "lettres, chiffres, points, tirets et soulignés, 100 caractères au plus, "
    "en commençant par une lettre ou un chiffre"
)


class ContractIdError(ValueError):
    """Identifiant de contrat invalide, refusé à la création du contrat."""


def check_contract_id(contract_id: str) -> str:
    """L'identifiant tel quel s'il suit la règle ; sinon `ContractIdError`, qui la
    rappelle."""
    if not CONTRACT_ID.fullmatch(contract_id):
        raise ContractIdError(
            f"identifiant de contrat invalide : {contract_id!r} ; {RULE}"
        )
    return contract_id
