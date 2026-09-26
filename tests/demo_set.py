"""Jeu de démonstration (`data/contracts/`) : lecture des contrats et de `attendus.yaml`.

Partagé par les tests du jeu (J4, T7) et du critère 9 (T8).
"""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from cdg.domain.models import Clause

DEMO = Path(__file__).resolve().parents[1] / "data" / "contracts"
EXPECTED = DEMO / "attendus.yaml"


@dataclass(frozen=True)
class DemoContract:
    id: str
    text: str
    parties: list[str]
    clauses: list[Clause]
    expected: dict[str, Any]
    injected: str | None = None  # paragraphe injecté (contrat piégé du critère 9)
    personal_data: list[str] = field(default_factory=list)
    realistic: bool = False  # rédigé de façon réaliste (J5), pas sans ambiguïté

    @property
    def rejected(self) -> bool:
        return "reject_reason" in self.expected

    @property
    def human(self) -> dict[str, str] | None:
        return self.expected.get("human")

    def clean_text(self) -> str:
        """Le même contrat sans le paragraphe injecté (critère 9)."""
        if self.injected is None or self.text.count(self.injected) != 1:
            raise ValueError(f"{self.id} : paragraphe injecté absent ou répété")
        return self.text.replace(self.injected, "")


def _clause(item: dict[str, Any]) -> Clause:
    return Clause(
        kind=item["kind"],
        present=item["present"],
        quote=item.get("quote", ""),
        value=item.get("value"),
        category=item.get("category"),
    )


def load() -> tuple[date, list[DemoContract]]:
    """Date d'analyse du jeu, et ses contrats dans l'ordre du fichier."""
    data = yaml.safe_load(EXPECTED.read_text(encoding="utf-8"))
    contracts = [
        DemoContract(
            id=cid,
            text=(DEMO / spec["file"]).read_text(encoding="utf-8"),
            parties=list(spec["parties"]),
            clauses=[_clause(item) for item in spec["clauses"]],
            expected=spec["expected"],
            injected=spec.get("injected"),
            personal_data=[str(p) for p in spec.get("personal_data", [])],
            realistic=spec.get("realistic", False),
        )
        for cid, spec in data["contracts"].items()
    ]
    return data["analysis_date"], contracts


def files() -> dict[str, str]:
    """Fichier de chaque contrat du jeu, par identifiant."""
    data = yaml.safe_load(EXPECTED.read_text(encoding="utf-8"))
    return {cid: spec["file"] for cid, spec in data["contracts"].items()}
