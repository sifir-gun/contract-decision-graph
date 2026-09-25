"""Règles par domaine : une fonction pure par domaine, sans appel réseau."""

from cdg.domain.rules._common import RuleFn
from cdg.domain.rules.conformite import conformite
from cdg.domain.rules.financier import financier
from cdg.domain.rules.juridique import juridique
from cdg.domain.rules.operationnel import operationnel
from cdg.domain.state import Domain

RULES: dict[Domain, RuleFn] = {
    "juridique": juridique,
    "financier": financier,
    "conformite": conformite,
    "operationnel": operationnel,
}
