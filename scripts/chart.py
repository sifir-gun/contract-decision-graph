"""Validation statique des charts Helm (ADR 005, PR C2 et C3) : l'application et le proxy
de sortie ; mêmes commandes en CI (job `chart`) et en local (`scripts/check.sh`).

  uv run --no-sync python scripts/chart.py verifier --dossier DOSSIER

1. helm à la version figée (installé en CI depuis l'archive officielle vérifiée par son
   empreinte ; sur le poste, par Homebrew) ;
2. `helm lint --strict` de chaque variante : schéma des valeurs, gabarits ;
3. rendu de chaque variante (réel, démonstration, repli par copie) dans DOSSIER ;
4. kubeconform : chaque manifeste rendu contre les schémas de Kubernetes, en mode strict ;
5. kube-linter, sur chaque variante à part : bonnes pratiques ; toute exception est
   justifiée sur l'objet concerné (annotation `ignore-check.kube-linter.io/…`) ou, si
   elle ne dépend d'aucun objet, dans `chart/.kube-linter.yaml`, et dans l'ADR 005.

kubeconform et kube-linter tournent dans leurs images officielles, figées par version et
empreinte. Réseau : schémas de Kubernetes (dépôt de kubeconform), registres des images.
"""

import argparse
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "chart" / "contract-decision-graph"
PROXY_CHART = ROOT / "chart" / "cdg-proxy"
LINTER_CONFIG = ROOT / "chart"
HELM_VERSION = "v4.3.0"
KUBERNETES = "1.36.4"  # canal stable de k3s au 27/09/2026
KUBECONFORM = (
    "ghcr.io/yannh/kubeconform:v0.8.0"
    "@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e"
)
KUBE_LINTER = (
    "docker.io/stackrox/kube-linter:v0.8.3"
    "@sha256:f2bfce7879206d32f69ab6572c376f916643f54ca291ac38cf7d01ef591ff3f9"
)
# variantes rendues et vérifiées : valeurs par défaut, démonstration, repli par copie
VARIANTS: dict[str, list[str]] = {
    "reel": [],
    "demo": ["--set", "mode=demo", "--set", "replicas=1"],
    "copie": ["--set", "modele.montage=copie"],
}
# proxy de sortie (PR C3) : l'empreinte de l'image n'a pas de valeur par défaut avant la
# première publication ; une empreinte d'exemple pour le rendu, qui ne tire rien
EXAMPLE_DIGEST = "sha256:" + "1" * 64
PROXY_VARIANTS: dict[str, list[str]] = {
    "production": ["--set", f"image.digest={EXAMPLE_DIGEST}"],
    "test": [
        "--set",
        f"image.digest={EXAMPLE_DIGEST}",
        "--set",
        "plagesAutorisees[0]=10.43.0.0/16",
        "--set",
        "sortiesInternes[0].espaceDeNoms=cdg-tests",
        "--set",
        "sortiesInternes[0].selecteur.app=mistral-factice",
        "--set",
        "sortiesInternes[0].port=8080",
    ],
}
# chaque chart, ses variantes, le préfixe de leurs rendus
CHARTS: list[tuple[Path, dict[str, list[str]], str]] = [
    (CHART, VARIANTS, ""),
    (PROXY_CHART, PROXY_VARIANTS, "proxy-"),
]

Run = Callable[..., subprocess.CompletedProcess[str]]


def _checked(
    run: Run, command: list[str], what: str
) -> subprocess.CompletedProcess[str]:
    result = run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{what} :\n{result.stdout}{result.stderr}".strip())
    return result


def verify(folder: Path, *, run: Run = subprocess.run) -> int:
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        version = _checked(
            run, ["helm", "version", "--template", "{{.Version}}"], "helm introuvable"
        ).stdout.strip()
        if version != HELM_VERSION:
            raise RuntimeError(
                f"helm {version} : {HELM_VERSION} attendu (celui de la CI, ADR 005)"
            )
        names = []
        for chart, variants, prefix in CHARTS:
            release = "cdg" if chart == CHART else chart.name
            for variant, options in variants.items():
                name = f"{prefix}{variant}"
                names.append(name)
                _checked(
                    run,
                    ["helm", "lint", "--strict", str(chart), *options],
                    f"helm lint ({name})",
                )
                rendered = _checked(
                    run,
                    ["helm", "template", release, str(chart), "--namespace", "cdg"]
                    + options,
                    f"helm template ({name})",
                )
                (folder / f"{name}.yaml").write_text(rendered.stdout, encoding="utf-8")
        _checked(
            run,
            ["docker", "run", "--rm", "-v", f"{folder}:/rendu:ro", KUBECONFORM]
            + ["-strict", "-summary", "-kubernetes-version", KUBERNETES, "/rendu"],
            "kubeconform",
        )
        # chaque variante à part : kube-linter relie les objets d'un même lot (budget
        # d'interruption et Deployment), et les rendus d'un chart portent les mêmes noms
        for name in names:
            _checked(
                run,
                ["docker", "run", "--rm", "-v", f"{folder}:/rendu:ro"]
                + ["-v", f"{LINTER_CONFIG}:/configuration:ro", KUBE_LINTER]
                + ["lint", "--config", "/configuration/.kube-linter.yaml"]
                + [f"/rendu/{name}.yaml"],
                f"kube-linter ({name})",
            )
    except RuntimeError as exc:
        print(f"chart refusé : {exc}", file=sys.stderr)
        return 1
    print(
        f"charts vérifiés : {len(CHARTS)} charts, {len(names)} variantes "
        f"(helm {HELM_VERSION}, Kubernetes {KUBERNETES})"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="commande", required=True)
    check = commands.add_parser(
        "verifier", help="lint, rendu, schémas, bonnes pratiques"
    )
    check.add_argument("--dossier", type=Path, required=True)
    args = parser.parse_args(argv)
    return verify(args.dossier)


if __name__ == "__main__":
    sys.exit(main())
