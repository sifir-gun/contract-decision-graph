"""Règles par domaine : une fonction pure par domaine, sans appel réseau. Elles passent
avant le CRAG et rendent un `Assessment` : constats rattachés à leur clause, score, blocage."""

from cdg.domain.models import Domain
from cdg.domain.rules._common import Assessment, RuleFinding, RuleFn
from cdg.domain.rules.conformite import conformite
from cdg.domain.rules.financier import financier
from cdg.domain.rules.juridique import juridique
from cdg.domain.rules.operationnel import operationnel

RULES: dict[Domain, RuleFn] = {
    "juridique": juridique,
    "financier": financier,
    "conformite": conformite,
    "operationnel": operationnel,
}

__all__ = ["RULES", "Assessment", "RuleFinding", "RuleFn"]
