"""Règles par domaine : une fonction pure par domaine, sans appel réseau."""

from cdg.rules._common import RuleFn
from cdg.rules.conformite import conformite
from cdg.rules.financier import financier
from cdg.rules.juridique import juridique
from cdg.rules.operationnel import operationnel
from cdg.state import Domain

RULES: dict[Domain, RuleFn] = {
    "juridique": juridique,
    "financier": financier,
    "conformite": conformite,
    "operationnel": operationnel,
}
