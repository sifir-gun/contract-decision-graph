"""Jeu de démonstration (`data/contracts/attendus.yaml`) : fichier, parties à masquer et
clauses qu'une extraction correcte rend, pour chaque contrat synthétique. L'interface web
en propose le choix ; son mode démonstration en tire l'extraction simulée."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from cdg.domain.models import Clause

CONTRACTS = Path(__file__).resolve().parents[3] / "data" / "contracts"
EXPECTED = CONTRACTS / "attendus.yaml"


@dataclass(frozen=True)
class DemoContract:
    id: str
    file: str
    parties: tuple[str, ...]
    clauses: tuple[Clause, ...]  # vide pour un contrat rejeté à l'entrée

    def text(self) -> str:
        return (CONTRACTS / self.file).read_text(encoding="utf-8")


def _clause(item: dict[str, Any]) -> Clause:
    """Clause attendue ; valeur, citation et catégorie omises valent vide."""
    return Clause(
        kind=item["kind"],
        present=item["present"],
        quote=item.get("quote", ""),
        value=item.get("value"),
        category=item.get("category"),
    )


def load(path: Path = EXPECTED) -> tuple[date, dict[str, DemoContract]]:
    """Date d'analyse des attendus, puis les contrats par identifiant."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    contracts = {
        cid: DemoContract(
            id=cid,
            file=item["file"],
            parties=tuple(item["parties"]),
            clauses=tuple(_clause(c) for c in item.get("clauses", [])),
        )
        for cid, item in data["contracts"].items()
    }
    return data["analysis_date"], contracts
