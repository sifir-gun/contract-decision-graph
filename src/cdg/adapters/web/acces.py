"""Journal des accès (PR D1 et D2, ADR 005), distinct du journal des décisions : refus
d'authentification, fournisseur d'identité injoignable, déconnexions ; refus
d'autorisation (rôle manquant, quatre yeux, second facteur manquant) et accès d'urgence
par la CLI. Une ligne par
événement sur la sortie standard, en JSON dans Kubernetes (`--journaux json`).

Jamais d'e-mail, de nom, de jeton ni de cookie : seuls les champs de la liste blanche
passent, et l'utilisateur n'y est désigné que par l'émetteur et l'identifiant stable (sub)
de son jeton vérifié. Les connexions sont tracées par oauth2-proxy, réglé pour n'écrire
que le sub (chart).
"""

import logging

log = logging.getLogger("cdg.acces")

EVENTS = frozenset(
    {
        "acces_refuse",
        "fournisseur_injoignable",
        "deconnexion",
        "acces_interdit",
        "quatre_yeux_refuse",
        "second_facteur_manquant",
        "acces_urgence",
    }
)
FIELDS = frozenset(
    {
        "motif",
        "iss",
        "sub",
        "methode",
        "chemin",
        "statut",
        "session_fournisseur",
        "role",
        "thread_id",
        "operateur",
        "commande",
    }
)


def event(name: str, **fields: str | int) -> None:
    if name not in EVENTS:
        raise ValueError(f"événement du journal des accès inconnu : {name}")
    refused = sorted(set(fields) - FIELDS)
    if refused:
        raise ValueError(
            f"champ refusé par le journal des accès : {', '.join(refused)} (liste "
            "blanche : jamais d'e-mail, de nom, de jeton ni de cookie)"
        )
    log.info(name, extra={"acces": {"evenement": name, **fields}})
