"""Chaîne d'approvisionnement de l'image (ADR 005, PR B) : mêmes commandes en CI et en local
(`scripts/check.sh`). Chaque outil tourne dans son image officielle, figée par version et
empreinte : rien à installer sur le poste, et le même binaire partout.

  uv run --no-sync python scripts/chaine.py bases
  uv run --no-sync python scripts/chaine.py revision
  uv run --no-sync python scripts/chaine.py inventaire IMAGE --dossier DOSSIER
  uv run --no-sync python scripts/chaine.py scan --dossier DOSSIER

- `bases` : vérifie la signature de chaque image de base du Dockerfile avant la
  construction ; une base sans politique de vérification est refusée, jamais ignorée.
- `revision` : commit passé à la construction de l'image (`--build-arg CDG_COMMIT`),
  scellé avec chaque décision : celui de HEAD si le contexte de construction est
  exactement celui du commit, sinon « inconnu », avec la raison sur la sortie d'erreur.
- `inventaire` : inventaire des composants de l'image construite (Syft), en SPDX (pour
  l'attestation) et au format de Syft (pour le scan), dans DOSSIER.
- `scan` : failles connues de cet inventaire (Grype) ; échec sur une faille critique ou
  haute qui a un correctif, sauf exception justifiée, datée et non expirée
  (`securite/exceptions-vulnerabilites.yaml`).
Réseau : registres des images, journal de transparence de Sigstore, API de GitHub, base
de failles de Grype (gardée dans le volume Docker `cdg-grype-db`).
"""

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from cdg.cli import today
from cdg.domain.version import UNKNOWN

ROOT = Path(__file__).resolve().parents[1]
EXCEPTIONS = ROOT / "securite" / "exceptions-vulnerabilites.yaml"
# images sans signature vérifiable, admises pour les tests seulement (ADR 008)
SIGNATURE_EXCEPTIONS = ROOT / "securite" / "exceptions-signatures.yaml"
# failles admises dans ces images de test, jamais dans le produit (ADR 008)
TEST_EXCEPTIONS = ROOT / "securite" / "exceptions-vulnerabilites-tests.yaml"
PINNED = re.compile(r"^[a-z0-9.-]+(:\d+)?(/[a-z0-9._-]+)+:[\w.-]+@sha256:[0-9a-f]{64}$")
MAX_EXCEPTION = timedelta(days=90)  # durée de vie d'une exception, au plus
GRYPE_DB = "cdg-grype-db"  # volume Docker : base de failles gardée entre deux scans

# outils : versions au-delà des avis de sécurité publiés (ADR 005, relevé du 28/09/2026)
COSIGN = (
    "ghcr.io/sigstore/cosign/cosign:v3.1.3"
    "@sha256:9e5c2f2edc34351160407ca3416c61855bdf9403c3c5936e0f0be7fc261611b8"
)
SYFT = (
    "docker.io/anchore/syft:v1.52.0"
    "@sha256:500e2d872ac019436926e8322b4fc1f39441d94d21f6f4046c6ff29b30e8cb02"
)
GRYPE = (
    "docker.io/anchore/grype:v0.119.0"
    "@sha256:8c2c9234a345577a6d321a4753aa3ee1276d8975c8452d2344a56b57733ecad3"
)

Run = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class Cosign:
    """Signature sans clé, vérifiée par cosign : identité et émetteur du certificat."""

    identity: str
    issuer: str

    def expected(self) -> str:
        return f"cosign : identité {self.identity}, émetteur {self.issuer}"

    def command(self, image: str) -> list[str]:
        return [
            "docker",
            "run",
            "--rm",
            COSIGN,
            "verify",
            image,
            "--certificate-identity",
            self.identity,
            "--certificate-oidc-issuer",
            self.issuer,
        ]


@dataclass(frozen=True)
class GitHubAttestation:
    """Attestation de provenance de GitHub, vérifiée par `gh attestation verify`."""

    owner: str
    workflow: str

    def expected(self) -> str:
        return (
            f"attestation GitHub : propriétaire {self.owner}, workflow {self.workflow}"
        )

    def command(self, image: str) -> list[str]:
        return [
            "gh",
            "attestation",
            "verify",
            f"oci://{image}",
            "--owner",
            self.owner,
            "--signer-workflow",
            self.workflow,
        ]


# qui doit avoir signé chaque base (documentation de chaque projet, ADR 005)
POLICIES: dict[str, Cosign | GitHubAttestation] = {
    "gcr.io/distroless/": Cosign(
        identity="keyless@distroless.iam.gserviceaccount.com",
        issuer="https://accounts.google.com",
    ),
    "ghcr.io/astral-sh/uv:": GitHubAttestation(
        owner="astral-sh",
        workflow="astral-sh/uv/.github/workflows/publish-docker-image.yml",
    ),
}


def bases(dockerfile: Path) -> list[str]:
    """Images de base (FROM), dans l'ordre du Dockerfile, chacune une fois. `FROM etape`
    reprend une étape précédente, y compris choisie par une variable (`go-${TARGETARCH}`) :
    ce n'est pas une base. Une variable qui ne désigne aucune étape est refusée : la base
    ne serait pas vérifiable."""
    found: list[str] = []
    stages: list[str] = []
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        words = line.split()
        if not words or words[0].upper() != "FROM":
            continue
        rest = [w for w in words[1:] if not w.startswith("--")]
        image = rest[0]
        # une variable (${VAR} ou $VAR) peut désigner n'importe quel nom d'étape
        pattern = re.sub(r"\\\$\\\{\w+\\\}|\\\$\w+", "[^/:@]+", re.escape(image))
        is_stage = any(re.fullmatch(pattern, stage) for stage in stages)
        if len(rest) >= 3 and rest[1].upper() == "AS":
            stages.append(rest[2])
        if is_stage:
            continue
        if "$" in image:
            raise ValueError(
                f"base choisie par une variable qui ne désigne aucune étape : {image}"
            )
        if image not in found:
            found.append(image)
    return found


def policy(image: str) -> Cosign | GitHubAttestation:
    for prefix, found in POLICIES.items():
        if image.startswith(prefix):
            return found
    raise ValueError(
        f"image de base sans politique de vérification de signature : {image} "
        "(scripts/chaine.py, POLICIES)"
    )


def command(image: str) -> list[str]:
    return policy(image).command(image)


# causes d'environnement reconnues dans le message d'un outil de vérification : la
# signature n'a été ni vérifiée ni refusée. Toute autre cause est une signature invalide,
# le cas le plus prudent (le 29/09, un échec réseau s'annonçait « signature refusée »).
UNVERIFIABLE = (
    (
        "erreur réseau",
        re.compile(
            r"no such host|dial tcp|i/o timeout|TLS handshake timeout"
            r"|connection (refused|reset)|network is unreachable"
            r"|temporary failure in name resolution|context deadline exceeded",
            re.IGNORECASE,
        ),
    ),
    (
        "service indisponible",
        re.compile(
            r"\b(HTTP|status code) (429|5\d\d)\b|TOOMANYREQUESTS", re.IGNORECASE
        ),
    ),
    ("gh non authentifié", re.compile(r"gh auth login")),
    ("Docker indisponible", re.compile(r"Cannot connect to the Docker daemon")),
)


def unverifiable(output: str) -> str | None:
    """Cause d'environnement d'un échec de vérification, si elle est reconnue."""
    for reason, pattern in UNVERIFIABLE:
        if pattern.search(output):
            return reason
    return None


def verify_bases(dockerfile: Path, *, run: Run = subprocess.run) -> int:
    """Signature de chaque base. Deux échecs, distingués, font échouer la vérification :
    signature invalide (erreur de sécurité) ou vérification impossible (environnement)."""
    invalid, impossible = [], []
    for image in bases(dockerfile):
        expected = policy(image)
        result = run(
            expected.command(image), capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            print(f"signature vérifiée : {image}")
            continue
        details = "\n".join(
            part.strip() for part in (result.stderr, result.stdout) if part.strip()
        )
        reason = unverifiable(details)
        if reason is None:
            invalid.append(image)
            print(
                f"signature invalide : {image}\n  erreur de sécurité : l'image n'est pas "
                f"signée comme attendu ({expected.expected()}) ; ne pas construire\n"
                f"{details}",
                file=sys.stderr,
            )
        else:
            impossible.append(image)
            print(
                f"vérification impossible : {image}\n  {reason} : la signature n'a été "
                f"ni vérifiée ni refusée ; relancer une fois la cause levée\n{details}",
                file=sys.stderr,
            )
    if invalid or impossible:
        print(
            f"bases non vérifiées : signatures invalides {len(invalid)}, "
            f"vérifications impossibles {len(impossible)}",
            file=sys.stderr,
        )
        return 1
    return 0


def inventory(image: str, folder: Path, *, run: Run = subprocess.run) -> int:
    """`image.tar`, puis `sbom.syft.json` et `sbom.spdx.json`, dans `folder`."""
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    for command in (
        ["docker", "save", image, "-o", str(folder / "image.tar")],
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{folder}:/travail",
            SYFT,
            "docker-archive:/travail/image.tar",
            "-o",
            "syft-json=/travail/sbom.syft.json",
            "-o",
            "spdx-json=/travail/sbom.spdx.json",
            "-q",
        ],
    ):
        result = run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            print(
                f"inventaire impossible ({command[1]}) :\n{result.stderr.strip()}",
                file=sys.stderr,
            )
            return 1
    print(f"inventaire : {folder / 'sbom.spdx.json'}")
    return 0


def _exception_rule(entry: dict[str, Any], today: date) -> dict[str, Any]:
    """Règle d'exception de Grype, après contrôle : justifiée, datée, non expirée."""
    name = entry.get("vulnerabilite") or "?"
    package = entry.get("paquet") or {}
    for field in ("nom", "type", "version"):
        if not package.get(field):
            raise ValueError(f"exception {name} : paquet sans {field}")
    reason = str(entry.get("motif") or "").strip()
    if not reason:
        raise ValueError(f"exception {name} : motif absent")
    decided, expires = entry.get("decidee"), entry.get("expire")
    if not isinstance(decided, date) or not isinstance(expires, date):
        raise TypeError(f"exception {name} : dates decidee et expire attendues")
    if expires - decided > MAX_EXCEPTION:
        raise ValueError(
            f"exception {name} : plus de 90 jours entre décision et expiration"
        )
    if today > expires:
        raise ValueError(
            f"exception {name} expirée le {expires.isoformat()} : la revoir "
            f"({EXCEPTIONS.relative_to(ROOT)})"
        )
    return {
        "vulnerability": name,
        "package": {
            "name": package["nom"],
            "type": package["type"],
            "version": str(package["version"]),
        },
        "reason": reason,
    }


def signature_exceptions(path: Path, today: date) -> dict[str, str]:
    """Images admises sans signature, pour les tests seulement (ADR 008) : image par
    étiquette et empreinte, portée « tests », motif, 90 jours au plus, non expirée ; une
    entrée qui manque à l'une de ces règles lève, nommée."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    allowed: dict[str, str] = {}
    for entry in data.get("exceptions") or []:
        image = str(entry.get("image") or "?")
        if not PINNED.match(image):
            raise ValueError(f"exception {image} : image par étiquette et empreinte")
        if entry.get("portee") != "tests":
            raise ValueError(f"exception {image} : portée tests seulement (ADR 008)")
        reason = str(entry.get("motif") or "").strip()
        if not reason:
            raise ValueError(f"exception {image} : motif absent")
        decided, expires = entry.get("decidee"), entry.get("expire")
        if not isinstance(decided, date) or not isinstance(expires, date):
            raise TypeError(f"exception {image} : dates decidee et expire attendues")
        if decided > today:
            raise ValueError(
                f"exception {image} : décision à venir ({decided.isoformat()})"
            )
        if expires < decided:
            raise ValueError(f"exception {image} : expiration avant sa décision")
        if expires - decided > MAX_EXCEPTION:
            raise ValueError(
                f"exception {image} : plus de 90 jours entre décision et expiration"
            )
        if today > expires:
            raise ValueError(
                f"exception {image} expirée le {expires.isoformat()} : la revoir "
                f"({SIGNATURE_EXCEPTIONS.relative_to(ROOT)})"
            )
        allowed[image] = reason
    return allowed


def grype_config(exceptions: Path, today: date) -> dict[str, Any]:
    data = yaml.safe_load(exceptions.read_text(encoding="utf-8")) or {}
    entries = data.get("exceptions") or []
    return {"ignore": [_exception_rule(entry, today) for entry in entries]}


def scan(
    folder: Path, exceptions: Path, *, today: date, run: Run = subprocess.run
) -> int:
    """Échec (1) sur une faille critique ou haute corrigeable, hors exception."""
    folder = folder.resolve()
    config = grype_config(exceptions, today)
    (folder / "grype.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{folder}:/travail",
        "-v",
        f"{GRYPE_DB}:/cache",
        "-e",
        "GRYPE_DB_CACHE_DIR=/cache",
        "-e",
        "GRYPE_CHECK_FOR_APP_UPDATE=false",
        GRYPE,
        "sbom:/travail/sbom.syft.json",
        "--only-fixed",
        "--fail-on",
        "high",
        "-c",
        "/travail/grype.yaml",
        "-o",
        "table",
        "-o",
        "json=/travail/grype.json",
        "-q",
    ]
    result = run(command, capture_output=True, text=True, check=False)
    print(result.stdout, end="")
    if result.returncode == 2:  # --fail-on : faille au-dessus du seuil
        print(
            "scan : faille critique ou haute corrigeable (tableau ci-dessus) ; "
            "corriger, ou justifier une exception datée",
            file=sys.stderr,
        )
        return 1
    if result.returncode != 0:
        print(f"scan impossible :\n{result.stderr.strip()}", file=sys.stderr)
        return 1
    print(
        f"scan : aucune faille critique ou haute corrigeable ({len(config['ignore'])} "
        "exception(s))"
    )
    return 0


def scan_test_images(folder: Path, *, today: date, run: Run = subprocess.run) -> int:
    """Images de test sans signature (exception limitée aux tests, ADR 008) : chacune
    tirée, inventoriée, scannée contre les exceptions des tests, puis retirée si elle
    n'était pas déjà là. Toutes passent avant l'échec (décision du 06/10 : la liste
    complète, pas la première seulement) ; une faille critique ou haute corrigeable non
    couverte, ou une image qui ne peut être tirée, la refuse, et le bilan nomme chaque
    image refusée."""
    images = sorted(signature_exceptions(SIGNATURE_EXCEPTIONS, today))
    refused: list[str] = []
    for image in images:
        target = folder / image.split("/")[-1].split(":")[0]
        present = run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            check=False,
        )
        if present.returncode != 0:
            pulled = run(
                ["docker", "pull", image], capture_output=True, text=True, check=False
            )
            if pulled.returncode != 0:
                print(
                    f"tirage impossible : {image}\n{pulled.stderr.strip()}",
                    file=sys.stderr,
                )
                refused.append(image)
                continue
        code = inventory(image, target, run=run)
        (target / "image.tar").unlink(
            missing_ok=True
        )  # disque du runner, pour le cluster
        if code == 0:
            code = scan(target, TEST_EXCEPTIONS, today=today, run=run)
        if present.returncode != 0:
            run(
                ["docker", "image", "rm", image],
                capture_output=True,
                text=True,
                check=False,
            )
        if code != 0:
            print(f"image de test refusée : {image}", file=sys.stderr)
            refused.append(image)
    if refused:
        print(
            f"images de test refusées : {len(refused)} sur {len(images)} "
            f"({', '.join(refused)})",
            file=sys.stderr,
        )
        return 1
    print(
        f"images de test : {len(images)} scannées, sans faille critique ou haute corrigeable non couverte"
    )
    return 0


# --- révision fournie à la construction ----------------------------------------------------


def build_context(root: Path) -> tuple[list[str], list[str]]:
    """Chemins admis dans le contexte de construction, et motifs qui en sont exclus, lus
    dans `.dockerignore` (tout exclure, puis admettre) ; tout autre motif est refusé."""
    admitted, excluded = [], []
    for raw in (root / ".dockerignore").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line == "*":
            continue
        if line.startswith("!"):
            admitted.append(line[1:].rstrip("/"))
        elif line.startswith("**/"):
            excluded.append(line[3:])
        else:
            raise ValueError(f".dockerignore : motif non pris en charge : {line}")
    return admitted, excluded


def _changed(porcelain: str) -> list[str]:
    """Chemins de `git status --porcelain -z` ; un renommage donne aussi l'ancien."""
    fields, paths = porcelain.split("\0"), []
    while fields:
        entry = fields.pop(0)
        if not entry:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC":
            paths.append(fields.pop(0))
    return paths


def revision(root: Path = ROOT, *, run: Run = subprocess.run) -> str:
    """Commit de la construction, passé à l'image (CDG_COMMIT) et scellé avec chaque
    décision : celui de HEAD si le contexte de construction est exactement celui du
    commit (ni modification, ni ajout, ni fichier ignoré par git mais copié) ; sinon
    « inconnu », avec la raison sur la sortie d'erreur. Jamais un commit qui ne décrirait
    pas le code copié."""
    admitted, excluded = build_context(root)
    head = run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode != 0:
        print("révision inconnue : aucun commit git", file=sys.stderr)
        return UNKNOWN
    status = run(
        ["git", "-C", str(root), "status", "--porcelain", "-z"]
        + ["--untracked-files=all", "--ignored", "--", *admitted],
        capture_output=True,
        text=True,
        check=True,
    )
    differing = [
        path
        for path in _changed(status.stdout)
        if not any(
            fnmatch(part, pattern)
            for part in PurePosixPath(path).parts
            for pattern in excluded
        )
    ]
    if differing:
        shown = ", ".join(differing[:5]) + (" …" if len(differing) > 5 else "")
        print(
            "révision inconnue : contexte de construction différent du commit "
            f"({shown})",
            file=sys.stderr,
        )
        return UNKNOWN
    return head.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="commande", required=True)
    verify = commands.add_parser("bases", help="signatures des images de base")
    verify.add_argument("--dockerfile", type=Path, default=ROOT / "Dockerfile")
    commands.add_parser("revision", help="commit passé à la construction de l'image")
    inventory_ = commands.add_parser("inventaire", help="inventaire (Syft)")
    inventory_.add_argument("image")
    inventory_.add_argument("--dossier", type=Path, required=True)
    tests_ = commands.add_parser(
        "images-de-test", help="images de test sans signature : inventaire et scan"
    )
    tests_.add_argument("--dossier", type=Path, required=True)
    scan_ = commands.add_parser("scan", help="failles connues (Grype)")
    scan_.add_argument("--dossier", type=Path, required=True)
    scan_.add_argument("--exceptions", type=Path, default=EXCEPTIONS)
    args = parser.parse_args(argv)
    if args.commande == "bases":
        return verify_bases(args.dockerfile)
    if args.commande == "revision":
        print(revision())
        return 0
    if args.commande == "inventaire":
        return inventory(args.image, args.dossier)
    if args.commande == "images-de-test":
        return scan_test_images(args.dossier, today=today())
    return scan(args.dossier, args.exceptions, today=today())


if __name__ == "__main__":
    sys.exit(main())
