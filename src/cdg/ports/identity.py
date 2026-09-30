"""Port de vérification de l'identité (PR D1) : un jeton d'identité signé, transmis par
oauth2-proxy, est vérifié avant que l'application ne croie l'identité qu'il porte.
Adaptateur réel : `adapters/oidc.py` (PyJWT)."""

from typing import Protocol

from cdg.domain.identity import Identity

# motifs d'un refus : journal des accès, jamais le jeton ni son contenu
REJECTION_REASONS = frozenset(
    {
        "jeton_absent",
        "jeton_illisible",
        "algorithme_refuse",
        "cle_inconnue",
        "signature_invalide",
        "jeton_expire",
        "jeton_pas_encore_valide",
        "emetteur_refuse",
        "audience_refusee",
        "revendication_absente",
        "revendication_invalide",
        "jeton_invalide",
    }
)


class IdentityRejected(Exception):
    """Jeton refusé : l'identité n'est pas crue (401)."""

    def __init__(self, reason: str) -> None:
        if reason not in REJECTION_REASONS:
            raise ValueError(f"motif de refus inconnu : {reason}")
        super().__init__(reason)
        self.reason = reason


class ProviderUnavailable(Exception):
    """Fournisseur d'identité injoignable : le jeton n'est ni accepté ni refusé (503)."""


class IdentityVerifier(Protocol):
    def verify(self, token: str) -> Identity:
        """Identité d'un jeton vérifié ; `IdentityRejected` ou `ProviderUnavailable`."""
        ...

    def end_session_endpoint(self) -> str | None:
        """Adresse de fin de session du fournisseur, s'il en publie une."""
        ...
