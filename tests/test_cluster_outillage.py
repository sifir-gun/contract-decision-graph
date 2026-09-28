"""Outillage du cluster de test (ADR 005, PR C3), vérifié sans cluster : versions figées
par empreinte, manifestes tiers contrôlés et réécrits pour tirer chaque image par
empreinte, profils CI et local réduit."""

import base64
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIGEST = re.compile(
    r"^[a-z0-9.-]+(:\d+)?(/[a-z0-9._-]+)+(:[\w.-]+)?@sha256:[0-9a-f]{64}$"
)


def cluster():
    spec = importlib.util.spec_from_file_location(
        "cluster", ROOT / "scripts" / "cluster.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_images_des_noeuds_et_du_registre_figees():
    module = cluster()
    assert module.K3S.startswith("rancher/k3s:v1.36.4-k3s1@sha256:")
    assert module.REGISTRY.startswith("docker.io/library/registry:3@sha256:")
    for image in (module.K3S, module.REGISTRY, module.SEAWEEDFS):
        assert DIGEST.fullmatch(image), image


def test_composants_tiers_figes_par_version_et_empreinte():
    module = cluster()
    assert set(module.MANIFESTS) == {"cert-manager", "cloudnative-pg", "barman-cloud"}
    versions = {name: m.url for name, m in module.MANIFESTS.items()}
    assert "/v1.21.2/cert-manager.yaml" in versions["cert-manager"]
    assert "/v1.30.1/cnpg-1.30.1.yaml" in versions["cloudnative-pg"]
    assert "/v0.15.0/manifest.yaml" in versions["barman-cloud"]
    for manifest in module.MANIFESTS.values():
        assert re.fullmatch(r"[0-9a-f]{64}", manifest.sha256)
    for tag, pinned in module.IMAGES.items():
        assert pinned.startswith(tag.rsplit(":", 1)[0] + "@sha256:"), tag
        assert DIGEST.fullmatch(pinned)


def test_manifeste_altere_refuse():
    module = cluster()
    manifest = module.Manifest("https://exemple.test/m.yaml", "0" * 64)
    with pytest.raises(module.ClusterError, match="empreinte"):
        manifest.checked(b"kind: Namespace\n")


def test_images_reecrites_par_empreinte_meme_en_argument_ou_dans_un_secret():
    module = cluster()
    tag = "ghcr.io/cloudnative-pg/plugin-barman-cloud-sidecar:v0.15.0"
    solver = "quay.io/jetstack/cert-manager-acmesolver:v1.21.2"
    encoded = base64.b64encode(tag.encode()).decode()
    text = (
        "kind: Deployment\nspec:\n  template:\n    spec:\n      containers:\n"
        "      - image: ghcr.io/cloudnative-pg/cloudnative-pg:1.30.1\n"
        f"        args: [--acme-http01-solver-image={solver}]\n"
        "---\nkind: Secret\ndata:\n"
        f"  SIDECAR_IMAGE: {encoded}\n"
    )
    pinned = module.repin(text)
    assert module.IMAGES["ghcr.io/cloudnative-pg/cloudnative-pg:1.30.1"] in pinned
    assert f"--acme-http01-solver-image={module.IMAGES[solver]}" in pinned
    secret = base64.b64encode(module.IMAGES[tag].encode()).decode()
    assert f"SIDECAR_IMAGE: {secret}" in pinned
    assert "cloudnative-pg:1.30.1\n" not in pinned


def test_image_inconnue_des_composants_refusee():
    module = cluster()
    with pytest.raises(module.ClusterError, match="exemple.test/inconnue:1"):
        module.repin("spec:\n  containers:\n  - image: exemple.test/inconnue:1\n")


def test_profils_ci_et_local_reduit():
    module = cluster()
    ci, local = module.PROFILES["ci"], module.PROFILES["local"]
    assert (ci.agents, ci.postgres_instances) == (2, 2)
    assert (local.agents, local.postgres_instances) == (1, 1)
    # plusieurs nœuds dans les deux profils : la répartition des réplicas est éprouvée
    assert ci.agents >= 1 and local.agents >= 1
    # profil local réduit : les requêtes de mémoire de l'interface sont abaissées
    assert local.web_memory_request < ci.web_memory_request
