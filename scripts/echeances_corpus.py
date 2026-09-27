"""Échéances du corpus : échoue si une source cesse d'être valide dans les N jours.

Lancé par le job `audit` de la CI, à chaque pull request et chaque lundi (déclenchement
planifié), et par `scripts/check.sh` :

    uv run --no-sync python scripts/echeances_corpus.py --jours 60

Code 1 et la liste des sources, avec leur fin de validité, si l'une expire dans
l'horizon, déjà expirées comprises : le corpus et les fiches sont à mettre à jour
(`docs/exploitation.md`, « Corpus et versions des textes »). Aucun réseau, aucune base.
"""

import argparse
import json
import sys
from datetime import date

from cdg.application.ingestion import source_validities
from cdg.cli import today
from cdg.domain.corpus import expiring


def positive(text: str) -> int:
    days = int(text)
    if days <= 0:
        raise ValueError(text)
    return days


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--jours", type=positive, required=True, help="horizon, en jours (entier > 0)"
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="date de référence, AAAA-MM-JJ (défaut : aujourd'hui, heure de Paris)",
    )
    args = parser.parse_args(argv)
    on = args.date or today()
    found = expiring(source_validities(), on, args.jours)
    result = {
        "date": on.isoformat(),
        "horizon_jours": args.jours,
        "echeances": [
            {"source": source, "fin_de_validite": end.isoformat()}
            for source, end in found
        ],
    }
    if found:
        error = f"sources du corpus qui expirent dans les {args.jours} jours"
        print(
            json.dumps({"erreur": error, **result}, ensure_ascii=False), file=sys.stderr
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
