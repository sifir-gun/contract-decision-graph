"""Image du modèle d'embedding (ADR 005, PR B) : vérifie qu'un cache rempli par
`cdg.cli fetch-embedding-model` contient exactement les fichiers figés par leurs empreintes
(`docker/modele/empreintes.sha256`), avant d'en faire une image.

  uv run --no-sync python scripts/modele.py verifier DOSSIER

Les empreintes figent le contenu, pas la révision du dépôt Hugging Face : celle-ci change
sans toucher aux poids (README, 24/09/2026). Un fichier modifié, absent ou en trop dans la
copie à plat fait échouer la vérification, avec son nom.
"""

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMPREINTES = ROOT / "docker" / "modele" / "empreintes.sha256"
CHUNK = 1 << 20


def expected(manifest: Path) -> dict[str, str]:
    """Chemin relatif au cache -> empreinte SHA-256 (format de `sha256sum`)."""
    entries = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, path = line.split(maxsplit=1)
            entries[path.strip()] = digest
    return entries


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def verify(folder: Path, manifest: Path = EMPREINTES) -> int:
    entries = expected(manifest)
    problems = []
    for relative, digest in sorted(entries.items()):
        path = folder / relative
        if not path.is_file():
            problems.append(f"absent : {relative}")
        elif sha256(path) != digest:
            problems.append(f"empreinte différente : {relative}")
    listed = {folder / relative for relative in entries}
    for directory in {path.parent for path in listed}:
        if directory.is_dir():
            extra = sorted(p.name for p in directory.iterdir() if p not in listed)
            problems += [
                f"en trop : {directory.relative_to(folder) / n}" for n in extra
            ]
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    revisions = sorted(folder.glob("models--*/refs/main"))
    shown = ", ".join(r.read_text(encoding="utf-8").strip() for r in revisions) or "?"
    print(f"modèle conforme : {len(entries)} fichiers (révision {shown})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="commande", required=True)
    check = commands.add_parser("verifier", help="le cache contre ses empreintes")
    check.add_argument("dossier", type=Path)
    check.add_argument("--empreintes", type=Path, default=EMPREINTES)
    args = parser.parse_args(argv)
    return verify(args.dossier, args.empreintes)


if __name__ == "__main__":
    sys.exit(main())
