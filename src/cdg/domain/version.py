"""Version du code qui décide, scellée avec chaque enregistrement v2 (PR D2, ADR 005).

Fournie, jamais devinée : le commit à la construction de l'image, l'empreinte de l'image
au lancement, par le chart. La racine de composition les lit ; le domaine ne fait que les
valider. Sans commit (poste de développement), le champ le dit : « inconnu ». Hors d'une
image lancée par le chart, aucune empreinte d'image (None).

Le rejeu fidèle exige le même code et la même configuration que la décision scellée ;
avec un autre code, c'est une réévaluation (ADR 005).
"""

import re

from pydantic import BaseModel, ConfigDict, field_validator

UNKNOWN = "inconnu"  # commit non fourni
_COMMIT = re.compile(r"[0-9a-f]{40}")  # empreinte SHA-1 d'un commit git, complète
_IMAGE = re.compile(r"sha256:[0-9a-f]{64}")  # empreinte d'une image OCI


class CodeVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    commit: str  # 40 caractères hexadécimaux, ou « inconnu »
    image: str | None  # sha256:…, celle que le chart déploie ; None hors du chart

    @field_validator("commit")
    @classmethod
    def _commit_complet_ou_inconnu(cls, value: str) -> str:
        if value != UNKNOWN and _COMMIT.fullmatch(value) is None:
            raise ValueError(
                "commit : 40 caractères hexadécimaux minuscules (empreinte complète), "
                f"ou « {UNKNOWN} »"
            )
        return value

    @field_validator("image")
    @classmethod
    def _empreinte_seule(cls, value: str | None) -> str | None:
        if value is not None and _IMAGE.fullmatch(value) is None:
            raise ValueError(
                "image : empreinte sha256: suivie de 64 caractères hexadécimaux "
                "minuscules, sans dépôt ni étiquette"
            )
        return value
