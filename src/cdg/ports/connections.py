"""Connexions à la base : l'erreur que rend un adaptateur quand le pool est épuisé
(phase Kubernetes, ADR 005). L'interface la rend en 503, avec un délai avant de réessayer.
"""


class ConnectionsExhausted(Exception):
    """Aucune connexion libre à la base dans le délai : réessayer."""

    def __init__(self) -> None:
        super().__init__(
            "base de données saturée : aucune connexion libre dans le délai, réessayer"
        )
