"""Chaîne d'approvisionnement de l'image (ADR 005, PR B) : mêmes commandes en CI et en local
(`scripts/check.sh`). Chaque outil tourne dans son image officielle, figée par version et
empreinte : rien à installer sur le poste, et le même binaire partout.

  uv run --no-sync python scripts/chaine.py bases

`bases` : vérifie la signature de chaque image de base du Dockerfile avant la
construction ; une base sans politique de vérification est refusée, jamais ignorée.
Réseau : registres des images, journal de transparence de Sigstore, API de GitHub.
"""

import argparse
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

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
    """Images de base (FROM), dans l'ordre du Dockerfile."""
    found = []
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        words = line.split()
        if words and words[0].upper() == "FROM":
            found.append(next(w for w in words[1:] if not w.startswith("--")))
    return found


def command(image: str) -> list[str]:
    for prefix, policy in POLICIES.items():
        if image.startswith(prefix):
            return policy.command(image)
    raise ValueError(
        f"image de base sans politique de vérification de signature : {image} "
        "(scripts/chaine.py, POLICIES)"
    )


def verify_bases(dockerfile: Path, *, run: Run = subprocess.run) -> int:
    failed = []
    for image in bases(dockerfile):
        result = run(command(image), capture_output=True, text=True, check=False)
        if result.returncode == 0:
            print(f"signature vérifiée : {image}")
        else:
            failed.append(image)
            print(
                f"signature refusée : {image}\n{result.stderr.strip()}", file=sys.stderr
            )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="commande", required=True)
    verify = commands.add_parser("bases", help="signatures des images de base")
    verify.add_argument("--dockerfile", type=Path, default=ROOT / "Dockerfile")
    args = parser.parse_args(argv)
    return verify_bases(args.dockerfile)


if __name__ == "__main__":
    sys.exit(main())
