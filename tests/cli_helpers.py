"""Aides des tests de la CLI : appel de `cli.main` et lecture de sa sortie JSON, et
extraction qui laisse le domaine financier INSUFFISANT."""

import json

from doubles import ABSENT, FixedExtractor, clauses

from cdg import cli


def run_cli(capsys, *argv) -> tuple[int, dict]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, json.loads(out if code == 0 else err)


# financier INSUFFISANT : un constat (pénalités d'exécution absentes) que le corpus ne
# justifie pas
INSUFFICIENT = (FixedExtractor(clauses(penalites_execution=ABSENT)), {"financier"})
